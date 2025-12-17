from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import redis
import streamlit as st
import httpx
import pandas as pd
from sqlalchemy import create_engine, desc, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.storage.models import ReportRun
from app.storage.redis import daily_limit_key


@dataclass(frozen=True)
class AdminState:
    settings: Any
    engine: Any
    redis: Any


@st.cache_resource(show_spinner=False)
def _init_state() -> AdminState:
    settings = get_settings()
    # Streamlit часто перезапускает код/loop, поэтому админка работает в sync-режиме.
    # Преобразуем async URL (postgresql+asyncpg://) в sync URL (postgresql+psycopg2://).
    db_url = str(settings.database_url).replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=1800)
    r = redis.Redis.from_url(str(settings.redis_url), decode_responses=True)
    return AdminState(settings=settings, engine=engine, redis=r)


def _session(s: AdminState) -> Session:
    maker = sessionmaker(bind=s.engine)
    return maker()


def _fetch_overview(s: AdminState) -> dict[str, Any]:
    with _session(s) as session:
        total = session.scalar(select(func.count()).select_from(ReportRun))
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        last_24h = session.scalar(
            select(func.count()).select_from(ReportRun).where(ReportRun.created_at >= since_24h)
        )
        last_row = session.execute(select(ReportRun).order_by(desc(ReportRun.created_at)).limit(1)).scalars().first()
        return {"total": int(total or 0), "last_24h": int(last_24h or 0), "last_row": last_row}


def _fetch_recent_runs(s: AdminState, limit: int = 20) -> list[ReportRun]:
    with _session(s) as session:
        res = session.execute(select(ReportRun).order_by(desc(ReportRun.created_at)).limit(limit))
        return list(res.scalars().all())


def _fetch_top_users(s: AdminState, limit: int = 10) -> list[tuple[int, int]]:
    with _session(s) as session:
        stmt = (
            select(ReportRun.user_id, func.count().label("cnt"))
            .group_by(ReportRun.user_id)
            .order_by(desc("cnt"))
            .limit(limit)
        )
        res = session.execute(stmt)
        return [(int(uid), int(cnt)) for uid, cnt in res.all()]


def _get_daily_used_sync(s: AdminState, user_id: int) -> int:
    v = s.redis.get(daily_limit_key(user_id))
    try:
        return int(v or "0")
    except Exception:
        return 0


def _ping_postgres(s: AdminState) -> tuple[bool, str]:
    try:
        with _session(s) as session:
            session.execute(select(func.now()))
        return True, "ok"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def _ping_redis(s: AdminState) -> tuple[bool, str]:
    try:
        res = s.redis.ping()
        return bool(res), "ok" if res else "no"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def _ping_openai(s: AdminState) -> tuple[bool, str]:
    # Дешёвый запрос для проверки доступности/ключа. 200/401/429 считаем "доступен", но с пояснением.
    key = str(getattr(s.settings, "openai_api_key", "") or "")
    if not key:
        return False, "OPENAI_API_KEY пуст"
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            return True, "ok"
        if r.status_code in (401, 403):
            return False, f"auth_error (HTTP {r.status_code})"
        if r.status_code == 429:
            return False, "rate_limited/quota (HTTP 429)"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def _ping_perplexity(s: AdminState) -> tuple[bool, str]:
    # Не шлём chat/completions (может стоить денег). Делаем быстрый GET, чтобы проверить доступность хоста.
    key = str(getattr(s.settings, "perplexity_api_key", "") or "")
    endpoint = str(getattr(s.settings, "perplexity_endpoint", "") or "")
    if not endpoint:
        return False, "PERPLEXITY_ENDPOINT пуст"
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(endpoint, headers={"Authorization": f"Bearer {key}"} if key else None)
        # 200/401/405/404 — хост жив, просто метод/права не те.
        if r.status_code in (200, 401, 403, 404, 405):
            return True, f"reachable (HTTP {r.status_code})"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def _fetch_daily_series(s: AdminState, days: int = 14) -> pd.DataFrame:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with _session(s) as session:
        stmt = (
            select(func.date_trunc("day", ReportRun.created_at).label("day"), func.count().label("reports"), func.count(func.distinct(ReportRun.user_id)).label("users"))
            .where(ReportRun.created_at >= since)
            .group_by("day")
            .order_by("day")
        )
        rows = session.execute(stmt).all()
    df = pd.DataFrame(rows, columns=["day", "reports", "users"])
    if not df.empty:
        df["day"] = pd.to_datetime(df["day"], utc=True)
    return df


def _fetch_error_events(s: AdminState, limit: int = 200) -> list[dict[str, Any]]:
    items = s.redis.lrange("admin:errors:recent", 0, max(0, limit - 1))
    out: list[dict[str, Any]] = []
    for raw in items:
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _errors_to_df(events: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for e in events:
        ts = e.get("ts")
        try:
            dtv = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc) if ts else None
        except Exception:
            dtv = None
        rows.append(
            {
                "ts": dtv,
                "service": e.get("service"),
                "user_id": e.get("user_id"),
                "report_id": e.get("report_id"),
                "request_id": e.get("request_id"),
                "message": e.get("message"),
                "exc_type": e.get("exc_type"),
                "exc": e.get("exc"),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty and "ts" in df.columns:
        df = df.sort_values("ts", ascending=False)
    return df


def main() -> None:
    st.set_page_config(page_title="DiplomatAI Admin", layout="wide")
    st.title("DiplomatAI — админка (Streamlit)")

    s = _init_state()

    with st.sidebar:
        st.subheader("Настройки")
        st.caption("Данные берутся из Postgres/Redis, которые используются ботом.")
        refresh = st.button("Обновить", type="primary")
        st.divider()
        st.caption("ENV")
        st.code(
            "\n".join(
                [
                    f"DATABASE_URL={s.settings.database_url}",
                    f"REDIS_URL={s.settings.redis_url}",
                    f"FREE_DAILY_LIMIT={s.settings.free_daily_limit}",
                    f"DISABLE_RATE_LIMIT={s.settings.disable_rate_limit}",
                ]
            ),
            language="text",
        )

    if refresh:
        st.cache_data.clear()

    tab_overview, tab_services, tab_charts, tab_errors = st.tabs(["Обзор", "Сервисы", "Графики", "Ошибки"])

    with tab_overview:
        overview = _fetch_overview(s)
        c1, c2, c3 = st.columns(3)
        c1.metric("Всего отчётов", overview["total"])
        c2.metric("Отчётов за 24ч", overview["last_24h"])
        last_row: ReportRun | None = overview["last_row"]
        c3.metric("Последний отчёт", (last_row.created_at.isoformat() if last_row else "—"))

        st.divider()
        left, right = st.columns([2, 1])

        with left:
            st.subheader("Последние отчёты")
            runs = _fetch_recent_runs(s, limit=25)
            if not runs:
                st.info("Пока нет сохранённых отчётов.")
            else:
                for r in runs:
                    with st.expander(f"{r.created_at.isoformat()} • user_id={r.user_id} • {r.topic[:80]}"):
                        st.caption("Query")
                        st.code(r.query, language="text")
                        st.caption("Report")
                        st.text_area("Текст отчёта", r.report_text, height=220)

        with right:
            st.subheader("Топ пользователей")
            top = _fetch_top_users(s, limit=15)
            if not top:
                st.info("Нет данных.")
            else:
                st.table([{"user_id": uid, "reports": cnt} for uid, cnt in top])

            st.divider()
            st.subheader("Лимиты (Redis)")
            uid = st.number_input("user_id", min_value=1, value=int((top[0][0] if top else 1)))
            used = _get_daily_used_sync(s, int(uid))
            st.metric("Использовано сегодня", used)

    with tab_services:
        st.subheader("Статус сервисов (ping)")
        ok_pg, msg_pg = _ping_postgres(s)
        ok_r, msg_r = _ping_redis(s)
        ok_o, msg_o = _ping_openai(s)
        ok_p, msg_p = _ping_perplexity(s)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Postgres", "OK" if ok_pg else "FAIL", msg_pg)
        c2.metric("Redis", "OK" if ok_r else "FAIL", msg_r)
        c3.metric("OpenAI", "OK" if ok_o else "WARN/FAIL", msg_o)
        c4.metric("Perplexity", "OK" if ok_p else "WARN/FAIL", msg_p)

        st.caption("OpenAI/Perplexity: 401/429 — это обычно проблема ключа/квоты, а не “падение сервиса”.")

    with tab_charts:
        st.subheader("Графики по дням")
        days = st.slider("Период (дней)", min_value=3, max_value=60, value=14)
        df = _fetch_daily_series(s, days=int(days))
        if df.empty:
            st.info("Нет данных за выбранный период.")
        else:
            dfx = df.set_index("day")
            st.caption("Отчёты/день")
            st.line_chart(dfx[["reports"]])
            st.caption("Уникальные пользователи/день")
            st.line_chart(dfx[["users"]])

        st.divider()
        st.subheader("Ошибки/день (из Redis error events)")
        events = _fetch_error_events(s, limit=500)
        if not events:
            st.info("Пока нет сохранённых ошибок (Redis).")
        else:
            edf = _errors_to_df(events)
            edf2 = edf.dropna(subset=["ts"]).copy()
            if edf2.empty:
                st.info("Ошибки есть, но без ts.")
            else:
                edf2["day"] = edf2["ts"].dt.floor("D")
                g = edf2.groupby("day").size().reset_index(name="errors")
                g["day"] = pd.to_datetime(g["day"], utc=True)
                g = g.set_index("day")
                st.line_chart(g[["errors"]])

    with tab_errors:
        st.subheader("Логи ошибок (последние)")
        limit = st.slider("Сколько записей загрузить", min_value=20, max_value=500, value=150)
        user_filter = st.text_input("Фильтр user_id (опционально)", value="")
        report_filter = st.text_input("Фильтр report_id (опционально)", value="")

        events = _fetch_error_events(s, limit=int(limit))
        df = _errors_to_df(events)
        if user_filter.strip():
            df = df[df["user_id"].astype(str) == user_filter.strip()]
        if report_filter.strip():
            df = df[df["report_id"].astype(str) == report_filter.strip()]

        if df.empty:
            st.info("Нет ошибок по фильтру.")
        else:
            st.dataframe(df[["ts", "service", "user_id", "report_id", "message", "exc_type"]], use_container_width=True)
            st.divider()
            st.subheader("Детали")
            for e in events[: min(len(events), 50)]:
                uid = str(e.get("user_id") or "")
                rid = str(e.get("report_id") or "")
                if user_filter.strip() and uid != user_filter.strip():
                    continue
                if report_filter.strip() and rid != report_filter.strip():
                    continue
                ts = e.get("ts")
                try:
                    dtv = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat() if ts else "—"
                except Exception:
                    dtv = "—"
                title = f"{dtv} • {e.get('service')} • user_id={uid or '—'} • {e.get('message')}"
                with st.expander(title):
                    st.code(json.dumps(e, ensure_ascii=False, indent=2), language="json")
                    tb = e.get("traceback")
                    if tb:
                        st.caption("Traceback (tail)")
                        st.code(tb, language="text")


if __name__ == "__main__":
    main()