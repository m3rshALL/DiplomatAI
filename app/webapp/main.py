from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qsl

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.services.openai_client import OpenAIRateLimitError, OpenAITemporaryError, OpenAIClient
from app.services.perplexity_client import PerplexityClient
from app.services.report_pipeline import ReportPipeline, RateLimitExceeded
from app.storage.db import create_engine_and_sessionmaker, init_db
from app.storage.redis import create_redis, push_error_event
from app.storage.repo import ReportRepo


log = get_logger(__name__)


def _verify_telegram_init_data(*, init_data: str, bot_token: str) -> dict[str, str]:
    """
    Проверка подписи initData от Telegram WebApp.
    Алгоритм: https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
    pairs = [(k, v) for k, v in parse_qsl(init_data, keep_blank_values=True) if k]
    data = {k: v for k, v in pairs}

    their_hash = data.get("hash")
    if not their_hash:
        raise ValueError("initData: отсутствует hash")

    items = [(k, v) for k, v in data.items() if k != "hash"]
    items.sort(key=lambda x: x[0])
    data_check_string = "\n".join([f"{k}={v}" for k, v in items])

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    our_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(our_hash, their_hash):
        raise ValueError("initData: неверная подпись")

    return data


def _extract_user_id(init_data_dict: dict[str, str]) -> int:
    raw_user = init_data_dict.get("user")
    if not raw_user:
        raise ValueError("initData: отсутствует user")
    obj = json.loads(raw_user)
    uid = obj.get("id")
    if not isinstance(uid, int):
        raise ValueError("initData: некорректный user.id")
    return uid


class ReportStartRequest(BaseModel):
    init_data: str = Field(..., description="Telegram.WebApp.initData")
    query: str
    send_to_chat: bool = True


class ReportStartResponse(BaseModel):
    job_id: str


class ReportStatusResponse(BaseModel):
    status: Literal["queued", "running", "done", "error"]
    stage: str | None = None
    result_text: str | None = None
    error: str | None = None


class FollowupStartRequest(BaseModel):
    init_data: str = Field(..., description="Telegram.WebApp.initData")
    base_text: str
    instruction: str
    send_to_chat: bool = True


class FollowupStartResponse(BaseModel):
    job_id: str


class HistoryRequest(BaseModel):
    init_data: str = Field(..., description="Telegram.WebApp.initData")
    limit: int = 10


class HistoryItemResponse(BaseModel):
    id: str
    created_at_iso: str
    topic: str
    query: str


class HistoryResponse(BaseModel):
    items: list[HistoryItemResponse]


class ReportTextRequest(BaseModel):
    init_data: str = Field(..., description="Telegram.WebApp.initData")
    report_id: str


class ReportTextResponse(BaseModel):
    report_text: str


@dataclass
class AppState:
    settings: Any
    pipeline: ReportPipeline
    redis: Any
    bot: Bot
    openai: OpenAIClient


app = FastAPI(title="DiplomatAI WebApp")


@app.on_event("startup")
async def _startup() -> None:
    # отдельный процесс, можно конфигурировать логирование независимо
    configure_logging()

    settings = get_settings()
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    await init_db(engine)

    r = create_redis(settings.redis_url)
    repo = ReportRepo(session_factory=session_factory)
    perplexity = PerplexityClient(
        api_key=settings.perplexity_api_key,
        endpoint=settings.perplexity_endpoint,
        model=settings.perplexity_model,
        timeout_seconds=settings.perplexity_timeout_seconds,
        retry_max_attempts=settings.retry_max_attempts,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
        use_response_format=settings.perplexity_use_response_format,
    )
    openai = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        retry_max_attempts=settings.retry_max_attempts,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
    )
    pipeline = ReportPipeline(settings=settings, redis=r, repo=repo, perplexity=perplexity, openai=openai)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    app.state.state = AppState(settings=settings, pipeline=pipeline, redis=r, bot=bot, openai=openai)
    log.info("webapp_ready")


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint for monitoring."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _set_job(*, r: Any, job_id: str, payload: dict[str, Any], ttl_seconds: int = 15 * 60) -> None:
    await r.set(f"webapp:job:{job_id}", json.dumps(payload, ensure_ascii=False, default=str), ex=ttl_seconds)


async def _get_job(*, r: Any, job_id: str) -> dict[str, Any] | None:
    raw = await r.get(f"webapp:job:{job_id}")
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def _send_to_chat(*, bot: Bot, user_id: int, text: str) -> None:
    # В личке chat_id == user_id
    for chunk in [text[i : i + 4000] for i in range(0, len(text), 4000)]:
        await bot.send_message(chat_id=user_id, text=chunk)


async def _run_job(*, job_id: str, user_id: int, query: str, send_to_chat: bool) -> None:
    st: AppState = app.state.state

    async def stage_cb(stage: str) -> None:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "running", "stage": stage})

    try:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "running", "stage": "searching"})
        text = await st.pipeline.run(user_id=user_id, query=query, stage_cb=stage_cb)
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "done", "result_text": text})
        if send_to_chat:
            await _send_to_chat(bot=st.bot, user_id=user_id, text=text)
    except RateLimitExceeded as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": f"Лимит исчерпан: {e}."})
    except OpenAIRateLimitError as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": str(e) or "OpenAI вернул 429."})
    except OpenAITemporaryError as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": str(e) or "OpenAI временно недоступен."})
    except Exception as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": "Не удалось сформировать отчёт. Попробуйте позже."})
        log.exception("webapp_report_failed")
        try:
            await push_error_event(
                st.redis,
                service="webapp",
                message="webapp_report_failed",
                user_id=user_id,
                request_id=None,
                report_id=None,
                extra={"job_id": job_id, "query": query},
                exc=e,
            )
        except Exception:
            pass


async def _run_followup_job(*, job_id: str, user_id: int, base_text: str, instruction: str, send_to_chat: bool) -> None:
    st: AppState = app.state.state
    try:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "running", "stage": "followup"})
        txt = await st.openai.followup_text(previous_report_text=base_text, instruction=instruction)  # type: ignore[attr-defined]
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "done", "result_text": txt})
        if send_to_chat:
            await _send_to_chat(bot=st.bot, user_id=user_id, text=txt)
    except OpenAIRateLimitError as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": str(e) or "OpenAI вернул 429."})
    except OpenAITemporaryError as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": str(e) or "OpenAI временно недоступен."})
    except Exception as e:
        await _set_job(r=st.redis, job_id=job_id, payload={"status": "error", "error": "Не удалось выполнить уточнение. Попробуйте позже."})
        log.exception("webapp_followup_failed")
        try:
            await push_error_event(
                st.redis,
                service="webapp",
                message="webapp_followup_failed",
                user_id=user_id,
                request_id=None,
                report_id=None,
                extra={"job_id": job_id},
                exc=e,
            )
        except Exception:
            pass


@app.post("/api/report", response_model=ReportStartResponse)
async def start_report(req: ReportStartRequest) -> ReportStartResponse:
    st: AppState = app.state.state

    try:
        init_data_dict = _verify_telegram_init_data(init_data=req.init_data, bot_token=st.settings.telegram_bot_token)
        user_id = _extract_user_id(init_data_dict)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e) or "Unauthorized") from None

    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Пустой запрос")

    job_id = str(uuid.uuid4())
    await _set_job(r=st.redis, job_id=job_id, payload={"status": "queued", "stage": None})
    asyncio.create_task(_run_job(job_id=job_id, user_id=user_id, query=query, send_to_chat=req.send_to_chat))
    return ReportStartResponse(job_id=job_id)


@app.post("/api/followup", response_model=FollowupStartResponse)
async def start_followup(req: FollowupStartRequest) -> FollowupStartResponse:
    st: AppState = app.state.state

    try:
        init_data_dict = _verify_telegram_init_data(init_data=req.init_data, bot_token=st.settings.telegram_bot_token)
        user_id = _extract_user_id(init_data_dict)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e) or "Unauthorized") from None

    base_text = (req.base_text or "").strip()
    instruction = (req.instruction or "").strip()
    if not base_text:
        raise HTTPException(status_code=400, detail="Пустой base_text")
    if not instruction:
        raise HTTPException(status_code=400, detail="Пустая instruction")

    job_id = str(uuid.uuid4())
    await _set_job(r=st.redis, job_id=job_id, payload={"status": "queued", "stage": None})
    asyncio.create_task(
        _run_followup_job(
            job_id=job_id,
            user_id=user_id,
            base_text=base_text,
            instruction=instruction,
            send_to_chat=req.send_to_chat,
        )
    )
    return FollowupStartResponse(job_id=job_id)

@app.get("/api/report/{job_id}", response_model=ReportStatusResponse)
async def get_report(job_id: str) -> ReportStatusResponse:
    st: AppState = app.state.state
    obj = await _get_job(r=st.redis, job_id=job_id)
    if not obj:
        raise HTTPException(status_code=404, detail="job not found")
    return ReportStatusResponse(
        status=str(obj.get("status") or "queued"),  # type: ignore[arg-type]
        stage=obj.get("stage"),
        result_text=obj.get("result_text"),
        error=obj.get("error"),
    )


@app.post("/api/history", response_model=HistoryResponse)
async def history(req: HistoryRequest) -> HistoryResponse:
    st: AppState = app.state.state
    try:
        init_data_dict = _verify_telegram_init_data(init_data=req.init_data, bot_token=st.settings.telegram_bot_token)
        user_id = _extract_user_id(init_data_dict)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e) or "Unauthorized") from None

    limit = max(1, min(50, int(req.limit)))
    rows = await st.pipeline.get_history(user_id=user_id)
    rows = rows[:limit]
    return HistoryResponse(
        items=[
            HistoryItemResponse(id=str(it.id), created_at_iso=it.created_at_iso, topic=it.topic, query=it.query)
            for it in rows
        ]
    )


@app.post("/api/report_text", response_model=ReportTextResponse)
async def report_text(req: ReportTextRequest) -> ReportTextResponse:
    st: AppState = app.state.state
    try:
        init_data_dict = _verify_telegram_init_data(init_data=req.init_data, bot_token=st.settings.telegram_bot_token)
        user_id = _extract_user_id(init_data_dict)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e) or "Unauthorized") from None

    try:
        report_id = uuid.UUID(req.report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный report_id") from None

    txt = await st.pipeline.get_report_text(user_id=user_id, report_id=report_id)
    if not txt:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return ReportTextResponse(report_text=txt)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    # Telegram требует HTTPS URL для WebApp.
    return """<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>DiplomatAI — Mini App</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
      :root {
        --bg: var(--tg-theme-bg-color, #0b0f14);
        --text: var(--tg-theme-text-color, #e6edf3);
        --hint: var(--tg-theme-hint-color, #9fb0c0);
        --btn: var(--tg-theme-button-color, #2f6feb);
        --btn-text: var(--tg-theme-button-text-color, #ffffff);
        --card: color-mix(in srgb, var(--bg) 88%, #ffffff 12%);
        --border: color-mix(in srgb, var(--bg) 78%, #ffffff 22%);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--bg);
        color: var(--text);
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Liberation Sans";
      }
      .wrap { max-width: 760px; margin: 0 auto; padding: 16px; }
      .header { display: flex; gap: 12px; align-items: center; }
      .badge {
        border: 1px solid var(--border);
        background: var(--card);
        padding: 6px 10px;
        border-radius: 999px;
        color: var(--hint);
        font-size: 12px;
      }
      h1 { font-size: 18px; margin: 12px 0 8px; }
      p { margin: 0 0 12px; color: var(--hint); line-height: 1.4; }
      .card {
        border: 1px solid var(--border);
        background: var(--card);
        border-radius: 14px;
        padding: 14px;
      }
      textarea {
        width: 100%;
        min-height: 110px;
        resize: vertical;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--text);
        padding: 12px;
        outline: none;
        font-size: 14px;
      }
      .row { display: flex; gap: 10px; margin-top: 10px; }
      button {
        flex: 1;
        border: none;
        border-radius: 12px;
        padding: 12px 14px;
        background: var(--btn);
        color: var(--btn-text);
        font-size: 14px;
        font-weight: 600;
      }
      .secondary {
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text);
        font-weight: 500;
      }
      .status { margin-top: 10px; color: var(--hint); font-size: 13px; }
      pre {
        margin-top: 12px;
        white-space: pre-wrap;
        word-break: break-word;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 12px;
        background: transparent;
        color: var(--text);
        display: none;
      }
      .actions {
        display: none;
        gap: 10px;
        margin-top: 10px;
        flex-wrap: wrap;
      }
      .actions button {
        flex: 1;
        min-width: 160px;
      }
      .inputrow {
        display: none;
        margin-top: 10px;
        gap: 10px;
      }
      input[type="text"] {
        width: 100%;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--text);
        padding: 12px;
        outline: none;
        font-size: 14px;
      }
      .meta { display:flex; gap:10px; margin-top:10px; align-items:center; color: var(--hint); font-size: 13px; }
      .meta label { display:flex; gap:8px; align-items:center; }
      .tabs { display:flex; gap:10px; margin-top: 10px; }
      .tabbtn {
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text);
        font-weight: 600;
      }
      .tabbtn.active {
        background: var(--btn);
        color: var(--btn-text);
        border-color: transparent;
      }
      .list {
        margin-top: 12px;
        display: none;
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
      }
      .item {
        padding: 12px;
        border-bottom: 1px solid var(--border);
      }
      .item:last-child { border-bottom: none; }
      .item .t { font-weight: 650; margin-bottom: 6px; }
      .item .s { color: var(--hint); font-size: 12px; line-height: 1.3; }
      .item button {
        margin-top: 10px;
        width: 100%;
      }
      .tips { margin-top: 12px; font-size: 12px; color: var(--hint); }
      .tips code { color: var(--text); }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="header">
        <div class="badge">Mini App</div>
        <div class="badge" id="themeBadge">theme</div>
      </div>
      <h1>DiplomatAI</h1>
      <p>Введите запрос — я отправлю его боту. Дальше прогресс и отчёт придут в чат.</p>

      <div class="card">
        <textarea id="q" placeholder="Например: Аналитика по рынку СПГ в Европе за 12 месяцев"></textarea>
        <div class="meta">
          <label><input id="sendToChat" type="checkbox" checked /> отправить результат в чат</label>
        </div>
        <div class="tabs">
          <button class="tabbtn active" id="tabNew">Новый отчёт</button>
          <button class="tabbtn" id="tabHistory">История</button>
        </div>
        <div class="row">
          <button id="send">Сформировать отчёт</button>
          <button class="secondary" id="clear">Очистить</button>
        </div>
        <div class="status" id="status"></div>
        <pre id="result"></pre>
        <div class="actions" id="actions">
          <button class="secondary" id="btnCompress">Сжать</button>
          <button class="secondary" id="btnContinue">Продолжить</button>
          <button class="secondary" id="btnClarify">Уточнить…</button>
        </div>
        <div class="inputrow" id="followupRow">
          <input id="followupText" type="text" placeholder="Инструкция: например, 'сфокусируйся только на trade impact'"/>
          <button id="btnApply">Применить</button>
        </div>
        <div class="list" id="history"></div>
        <div class="tips">
          Подсказка: Telegram требует <code>HTTPS</code> URL для Mini App. Для разработки используйте Cloudflare Tunnel/Ngrok.
        </div>
      </div>
    </div>

    <script>
      const tg = window.Telegram?.WebApp;
      const q = document.getElementById("q");
      const status = document.getElementById("status");
      const result = document.getElementById("result");
      const themeBadge = document.getElementById("themeBadge");
      const sendToChat = document.getElementById("sendToChat");
      const historyEl = document.getElementById("history");
      const tabNew = document.getElementById("tabNew");
      const tabHistory = document.getElementById("tabHistory");
      const sendBtn = document.getElementById("send");
      const clearBtn = document.getElementById("clear");
      const actions = document.getElementById("actions");
      const followupRow = document.getElementById("followupRow");
      const followupText = document.getElementById("followupText");
      const btnApply = document.getElementById("btnApply");
      const btnCompress = document.getElementById("btnCompress");
      const btnContinue = document.getElementById("btnContinue");
      const btnClarify = document.getElementById("btnClarify");

      function setStatus(s) { status.textContent = s || ""; }
      function setResult(s) {
        result.textContent = s || "";
        result.style.display = s ? "block" : "none";
        actions.style.display = s ? "flex" : "none";
        followupRow.style.display = s ? "flex" : "none";
      }
      function setHistoryVisible(v) {
        historyEl.style.display = v ? "block" : "none";
      }
      function setNewVisible(v) {
        q.style.display = v ? "block" : "none";
        document.querySelector(".meta").style.display = v ? "flex" : "none";
        document.querySelector(".row").style.display = v ? "flex" : "none";
      }
      function setActiveTab(isHistory) {
        tabHistory.classList.toggle("active", isHistory);
        tabNew.classList.toggle("active", !isHistory);
        setHistoryVisible(isHistory);
        setNewVisible(!isHistory);
        setStatus("");
        if (isHistory) setResult("");
      }

      if (tg) {
        tg.ready();
        tg.expand();
        themeBadge.textContent = (tg.colorScheme || "unknown");
      } else {
        themeBadge.textContent = "no-telegram";
        setStatus("Откройте страницу из Telegram, чтобы запускать отчёт.");
      }

      clearBtn.addEventListener("click", () => {
        q.value = "";
        setStatus("");
        setResult("");
        followupText.value = "";
      });

      tabNew.addEventListener("click", () => setActiveTab(false));
      tabHistory.addEventListener("click", () => {
        setActiveTab(true);
        loadHistory();
      });

      async function pollJob(jobId) {
        for (;;) {
          const r = await fetch(`/api/report/${jobId}`);
          const j = await r.json();
          if (j.status === "queued") {
            setStatus("В очереди…");
          } else if (j.status === "running") {
            if (j.stage === "searching") setStatus("Ищу источники…");
            else if (j.stage === "generating") setStatus("Формирую отчёт…");
            else if (j.stage === "followup") setStatus("Уточняю…");
            else setStatus("Работаю…");
          } else if (j.status === "done") {
            setStatus("Готово.");
            setResult(j.result_text || "");
            return;
          } else if (j.status === "error") {
            setStatus(j.error || "Ошибка.");
            setResult("");
            return;
          } else {
            setStatus("Неизвестный статус.");
            return;
          }
          await new Promise((res) => setTimeout(res, 900));
        }
      }

      async function loadHistory() {
        if (!tg) return;
        setStatus("Загружаю историю…");
        historyEl.innerHTML = "";
        try {
          const r = await fetch("/api/history", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ init_data: tg.initData || "", limit: 10 }),
          });
          const j = await r.json();
          if (!r.ok) {
            setStatus((j && j.detail) || "Не удалось загрузить историю.");
            return;
          }
          const items = (j && j.items) || [];
          if (!items.length) {
            historyEl.innerHTML = '<div class="item"><div class="t">История пуста</div><div class="s">Сформируйте первый отчёт.</div></div>';
            setStatus("");
            return;
          }
          for (const it of items) {
            const div = document.createElement("div");
            div.className = "item";
            const title = (it.topic || it.query || "Отчёт").slice(0, 120);
            const sub = `${(it.created_at_iso || "").slice(0, 19).replace("T", " ")} • ${String(it.query || "").slice(0, 160)}`;
            div.innerHTML = `<div class="t">${escapeHtml(title)}</div><div class="s">${escapeHtml(sub)}</div>`;
            const b = document.createElement("button");
            b.textContent = "Открыть";
            b.addEventListener("click", () => openReport(it.id));
            div.appendChild(b);
            historyEl.appendChild(div);
          }
          setStatus("");
        } catch {
          setStatus("Сетевая ошибка при загрузке истории.");
        }
      }

      function escapeHtml(s) {
        return String(s || "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#039;");
      }

      async function openReport(reportId) {
        if (!tg) return;
        setStatus("Открываю отчёт…");
        try {
          const r = await fetch("/api/report_text", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ init_data: tg.initData || "", report_id: reportId }),
          });
          const j = await r.json();
          if (!r.ok) {
            setStatus((j && j.detail) || "Не удалось открыть отчёт.");
            return;
          }
          setStatus("Готово.");
          setResult(j.report_text || "");
        } catch {
          setStatus("Сетевая ошибка при открытии отчёта.");
        }
      }

      async function runFollowup(instruction) {
        const base = (result.textContent || "").trim();
        const instr = String(instruction || "").trim();
        if (!base) {
          setStatus("Нет текста отчёта для уточнения.");
          return;
        }
        if (!instr) {
          setStatus("Введите инструкцию.");
          return;
        }
        setStatus("Запускаю уточнение…");
        setResult(base); // оставить текущий текст пока ждём
        try {
          const r = await fetch("/api/followup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              init_data: tg.initData || "",
              base_text: base,
              instruction: instr,
              send_to_chat: !!sendToChat.checked,
            }),
          });
          const j = await r.json();
          if (!r.ok) {
            setStatus((j && (j.detail || j.error)) || "Ошибка запуска уточнения.");
            return;
          }
          await pollJob(j.job_id);
        } catch {
          setStatus("Сетевая ошибка. Проверьте соединение.");
        }
      }

      btnCompress.addEventListener("click", () => runFollowup("Сожми отчёт в 8–12 буллетов. Сохрани ссылки/источники и предупреждения о недостатке данных."));
      btnContinue.addEventListener("click", () => runFollowup("Продолжи анализ: добавь углублённые выводы и чёткие практические рекомендации. Без новых фактов."));
      btnClarify.addEventListener("click", () => {
        followupText.focus();
        if (!followupText.value.trim()) followupText.value = "Уточни предыдущий отчёт: ";
      });
      btnApply.addEventListener("click", () => runFollowup(followupText.value));

      sendBtn.addEventListener("click", () => {
        const query = (q.value || "").trim();
        if (!query) {
          setStatus("Введите запрос.");
          return;
        }
        if (!tg) {
          setStatus("Нет Telegram.WebApp. Откройте Mini App из Telegram.");
          return;
        }
        setStatus("Запускаю…");
        setResult("");
        fetch("/api/report", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            init_data: tg.initData || "",
            query,
            send_to_chat: !!sendToChat.checked,
          }),
        })
          .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
          .then(({ ok, j }) => {
            if (!ok) {
              setStatus((j && (j.detail || j.error)) || "Ошибка запуска.");
              return;
            }
            pollJob(j.job_id);
          })
          .catch(() => setStatus("Сетевая ошибка. Проверьте соединение."));
      });

      // стартовый режим
      setActiveTab(false);
    </script>
  </body>
</html>
"""