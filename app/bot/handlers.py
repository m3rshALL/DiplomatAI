from __future__ import annotations

import asyncio
import json
import socket
import uuid

import httpx
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from redis.exceptions import RedisError
from urllib.parse import urlparse

from app.core.logging import get_logger, request_id_var
from app.services.openai_client import OpenAIRateLimitError, OpenAITemporaryError
from app.services.report_pipeline import ReportPipeline, RateLimitExceeded
from app.storage.redis import push_error_event
from app.storage.session import append_turn, build_query_with_context, get_session, set_pref


router = Router()
log = get_logger(__name__)

STATUS_SEARCHING = "Ищу источники…"
STATUS_GENERATING = "Формирую отчёт…"
STATUS_OPENAI_429 = "OpenAI вернул 429. Попробуйте позже."
STATUS_OPENAI_TEMP = "OpenAI временно недоступен. Попробуйте чуть позже."


async def _is_host_resolvable(hostname: str) -> bool:
    """
    trycloudflare URL часто "протухает" после перезапуска tunnel, и тогда Telegram показывает ERR_NAME_NOT_RESOLVED.
    Проверяем, что домен резолвится, чтобы не выдавать пользователю мёртвую ссылку.
    """
    if not hostname:
        return False
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.run_in_executor(None, socket.getaddrinfo, hostname, None), timeout=0.8)
        return True
    except (asyncio.TimeoutError, OSError):
        return False


async def _get_usable_webapp_url(pipeline: ReportPipeline) -> str | None:
    url = (pipeline.settings.webapp_url or "").strip()
    if not url:
        return None
    host = urlparse(url).hostname or ""
    if not host:
        return None
    if host.endswith("trycloudflare.com") and not await _is_host_resolvable(host):
        return None
    return url


async def _safe_edit_status(status: Message, text: str) -> None:
    # Telegram возвращает ошибку, если мы пытаемся "изменить" сообщение на то же самое.
    if (status.text or "") == text:
        return
    try:
        await status.edit_text(text)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _run_pipeline_with_status(*, message: Message, pipeline: ReportPipeline, query: str) -> None:
    status = await message.answer(STATUS_SEARCHING)

    async def stage_cb(stage: str) -> None:
        if stage == "searching":
            await _safe_edit_status(status, STATUS_SEARCHING)
        elif stage == "generating":
            await _safe_edit_status(status, STATUS_GENERATING)

    try:
        # Контекст/настройки пользователя (Redis)
        uid = message.from_user.id  # type: ignore[union-attr]
        sess = await get_session(pipeline.redis, user_id=uid)
        q_eff = build_query_with_context(query=query, session=sess)
        final_text = await pipeline.run(user_id=uid, query=q_eff, stage_cb=stage_cb)
    except RateLimitExceeded as e:
        await _safe_edit_status(status, f"Лимит исчерпан: {e}. Попробуйте завтра.")
        return
    except OpenAIRateLimitError as e:
        await _safe_edit_status(status, str(e) or STATUS_OPENAI_429)
        return
    except OpenAITemporaryError:
        await _safe_edit_status(status, STATUS_OPENAI_TEMP)
        return
    except (ValueError, httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, RuntimeError) as e:
        await _safe_edit_status(status, "Не удалось сформировать отчёт из-за ошибки. Попробуйте позже.")
        log.exception("report_failed")
        try:
            await push_error_event(
                pipeline.redis,
                service="bot",
                message="report_failed",
                user_id=(message.from_user.id if message.from_user else None),
                request_id=request_id_var.get(),
                report_id=None,
                extra={"query": query},
                exc=e,
            )
        except RedisError:
            pass
        return

    # отправляем отдельным сообщением, чтобы статус сохранился как прогресс
    await message.answer(final_text[:4000])

    # Обновляем историю запросов (после успешного запуска)
    try:
        await append_turn(
            pipeline.redis,
            user_id=uid,
            query=query,
            max_turns=pipeline.settings.session_max_turns,
            ttl_seconds=pipeline.settings.session_ttl_seconds,
        )
    except RedisError:
        pass


@router.message(Command("start"))
async def start_cmd(message: Message, pipeline: ReportPipeline) -> None:
    text = (
        "Привет! Я <b>AI-аналитик</b>.\n\n"
        "Пришлите запрос — я найду актуальные источники, соберу таблицу фактов и сформирую отчёт без выдумок.\n\n"
        "<b>Примеры:</b>\n"
        "- Аналитика по рынку СПГ в Европе за 12 месяцев\n"
        "- Краткий отчёт по динамике инфляции в РФ в 2024 с источниками\n"
        "- Обзор санкций ЕС против РФ: последние изменения и источники\n\n"
        "Команды: /help /history /limits /app"
    )
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="История", callback_data="cmd:history"),
            InlineKeyboardButton(text="Лимиты", callback_data="cmd:limits"),
        ]
    ]
    webapp_url = await _get_usable_webapp_url(pipeline)
    if webapp_url:
        buttons.insert(
            0,
            [InlineKeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=webapp_url))],
        )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb)

@router.message(Command("focus"))
async def focus_cmd(message: Message, pipeline: ReportPipeline) -> None:
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: /focus <фокус>. Пример: /focus trade impact only")
        return
    focus = parts[1].strip()
    await set_pref(
        pipeline.redis,
        user_id=message.from_user.id,  # type: ignore[union-attr]
        key="focus",
        value=focus,
        ttl_seconds=pipeline.settings.session_ttl_seconds,
    )
    await message.answer(f"Фокус установлен: <b>{focus}</b>.\nСледующие запросы будут учитывать это.")


@router.message(Command("continue"))
async def continue_cmd(message: Message, pipeline: ReportPipeline) -> None:
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    instruction = parts[1].strip() if len(parts) > 1 else "Продолжи анализ: углуби выводы и добавь чёткие пункты."

    status = await message.answer("Продолжаю анализ…")
    try:
        txt = await pipeline.followup_last_report(
            user_id=message.from_user.id,  # type: ignore[union-attr]
            instruction=instruction,
        )
    except ValueError as e:
        await _safe_edit_status(status, str(e) or "Нет предыдущего отчёта.")
        return
    except OpenAIRateLimitError as e:
        await _safe_edit_status(status, str(e) or STATUS_OPENAI_429)
        return
    except OpenAITemporaryError:
        await _safe_edit_status(status, STATUS_OPENAI_TEMP)
        return
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, RuntimeError):
        await _safe_edit_status(status, "Не удалось продолжить. Попробуйте позже.")
        return
    await _safe_edit_status(status, "Готово.")
    await message.answer(txt[:4000])


@router.message(Command("clarify"))
async def clarify_cmd(message: Message, pipeline: ReportPipeline) -> None:
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: /clarify <что уточнить>. Пример: /clarify уточни методологию и источники")
        return
    instruction = "Уточни предыдущий отчёт по запросу пользователя: " + parts[1].strip()

    status = await message.answer("Уточняю…")
    try:
        txt = await pipeline.followup_last_report(
            user_id=message.from_user.id,  # type: ignore[union-attr]
            instruction=instruction,
        )
    except ValueError as e:
        await _safe_edit_status(status, str(e) or "Нет предыдущего отчёта.")
        return
    except OpenAIRateLimitError as e:
        await _safe_edit_status(status, str(e) or STATUS_OPENAI_429)
        return
    except OpenAITemporaryError:
        await _safe_edit_status(status, STATUS_OPENAI_TEMP)
        return
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, RuntimeError):
        await _safe_edit_status(status, "Не удалось уточнить. Попробуйте позже.")
        return
    await _safe_edit_status(status, "Готово.")
    await message.answer(txt[:4000])


@router.message(Command("app"))
async def app_cmd(message: Message, pipeline: ReportPipeline) -> None:
    webapp_url = await _get_usable_webapp_url(pipeline)
    if not webapp_url:
        if (pipeline.settings.webapp_url or "").strip():
            await message.answer(
                "Mini App URL сейчас не открывается (похоже, trycloudflare домен устарел).\n\n"
                "Быстрое решение:\n"
                "1) Перезапустите tunnel: docker-compose restart tunnel\n"
                "2) Возьмите новый URL из логов: docker-compose logs tunnel\n"
                "3) Обновите WEBAPP_URL в .env и перезапустите bot: docker-compose restart bot\n\n"
                "На Windows можно автоматом обновить .env:\n"
                "scripts\\refresh_webapp_url.bat"
            )
            return
        await message.answer(
            "Mini App не настроен. Укажите WEBAPP_URL (HTTPS) и перезапустите контейнеры."
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=webapp_url))]
        ]
    )
    await message.answer("Откройте Mini App:", reply_markup=kb)


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    text = (
        "<b>Формат отчёта</b>:\n"
        "1) Краткое резюме (каждый пункт с цитированием)\n"
        "2) Контекст и ключевые факты (с источниками)\n"
        "3) Аналитика и выводы (только из фактов)\n"
        "4) Сценарии/прогноз (как допущения)\n"
        "5) Риски и пробелы\n"
        "6) Источники\n\n"
        "Если данных не хватает — я явно укажу пробелы и что нужно уточнить."
    )
    await message.answer(text)


@router.message(Command("limits"))
async def limits_cmd(message: Message, pipeline: ReportPipeline) -> None:
    remaining, limit = await pipeline.get_limits(user_id=message.from_user.id)  # type: ignore[union-attr]
    await message.answer(f"Лимит Free: {limit}/сутки. Осталось на сегодня: <b>{remaining}</b>.")


@router.message(Command("history"))
async def history_cmd(message: Message, pipeline: ReportPipeline) -> None:
    items = await pipeline.get_history(user_id=message.from_user.id)  # type: ignore[union-attr]
    if not items:
        await message.answer("История пуста. Пришлите запрос текстом — я сформирую первый отчёт.")
        return

    buttons: list[list[InlineKeyboardButton]] = []
    lines: list[str] = ["<b>Последние отчёты:</b>"]
    for it in items:
        short = (it.topic or it.query)[:70]
        lines.append(f"- {it.created_at_iso} — {short}")
        buttons.append([InlineKeyboardButton(text=f"Открыть {it.created_at_iso[:10]}", callback_data=f"rep:{it.id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("rep:"))
async def open_report_cb(callback: CallbackQuery, pipeline: ReportPipeline) -> None:
    raw = callback.data or ""
    try:
        rep_id = uuid.UUID(raw.split("rep:", 1)[1])
    except ValueError:
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    txt = await pipeline.get_report_text(user_id=callback.from_user.id, report_id=rep_id)
    if not txt:
        await callback.answer("Отчёт не найден.", show_alert=True)
        return

    await callback.message.answer(txt[:4000])  # Telegram limit safety
    await callback.answer()


@router.callback_query(F.data == "cmd:history")
async def history_shortcut_cb(callback: CallbackQuery, pipeline: ReportPipeline) -> None:
    if callback.message:
        await history_cmd(callback.message, pipeline)
    await callback.answer()


@router.callback_query(F.data == "cmd:limits")
async def limits_shortcut_cb(callback: CallbackQuery, pipeline: ReportPipeline) -> None:
    if callback.message:
        await limits_cmd(callback.message, pipeline)
    await callback.answer()


@router.message(F.web_app_data)
async def webapp_data(message: Message, pipeline: ReportPipeline) -> None:
    raw = (message.web_app_data.data or "").strip()  # type: ignore[union-attr]
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        await message.answer("Не удалось прочитать данные из Mini App.")
        return

    query = str(payload.get("query") or "").strip()
    if not query:
        await message.answer("Пустой запрос из Mini App.")
        return
    await _run_pipeline_with_status(message=message, pipeline=pipeline, query=query)


@router.message(F.text)
async def text_query(message: Message, pipeline: ReportPipeline) -> None:
    query = (message.text or "").strip()
    if not query:
        return
    await _run_pipeline_with_status(message=message, pipeline=pipeline, query=query)