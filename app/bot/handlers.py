from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.report_pipeline import ReportPipeline, RateLimitExceeded


router = Router()


@router.message(Command("start"))
async def start_cmd(message: Message) -> None:
    text = (
        "Привет! Я <b>AI-аналитик</b>.\n\n"
        "Пришлите запрос — я найду актуальные источники, соберу таблицу фактов и сформирую отчёт без выдумок.\n\n"
        "<b>Примеры:</b>\n"
        "- Аналитика по рынку СПГ в Европе за 12 месяцев\n"
        "- Краткий отчёт по динамике инфляции в РФ в 2024 с источниками\n"
        "- Обзор санкций ЕС против РФ: последние изменения и источники\n\n"
        "Команды: /help /history /limits"
    )
    await message.answer(text)


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
    except Exception:
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    txt = await pipeline.get_report_text(user_id=callback.from_user.id, report_id=rep_id)
    if not txt:
        await callback.answer("Отчёт не найден.", show_alert=True)
        return

    await callback.message.answer(txt[:4000])  # Telegram limit safety
    await callback.answer()


@router.message(F.text)
async def text_query(message: Message, pipeline: ReportPipeline) -> None:
    query = (message.text or "").strip()
    if not query:
        return

    status = await message.answer("Ищу источники…")

    async def stage_cb(stage: str) -> None:
        if stage == "searching":
            await status.edit_text("Ищу источники…")
        elif stage == "generating":
            await status.edit_text("Формирую отчёт…")

    try:
        final_text = await pipeline.run(user_id=message.from_user.id, query=query, stage_cb=stage_cb)  # type: ignore[union-attr]
    except RateLimitExceeded as e:
        await status.edit_text(f"Лимит исчерпан: {e}. Попробуйте завтра.")
        return
    except Exception:
        await status.edit_text("Не удалось сформировать отчёт из-за ошибки. Попробуйте позже.")
        raise

    # отправляем отдельным сообщением, чтобы статус сохранился как прогресс
    await message.answer(final_text[:4000])


