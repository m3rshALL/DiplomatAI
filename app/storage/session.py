from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis


def session_key(user_id: int) -> str:
    return f"session:{user_id}"


@dataclass
class UserSession:
    turns: list[str]
    prefs: dict[str, str]


async def get_session(r: redis.Redis, *, user_id: int) -> UserSession:
    raw = await r.get(session_key(user_id))
    if not raw:
        return UserSession(turns=[], prefs={})
    try:
        obj = json.loads(raw)
        turns = obj.get("turns") if isinstance(obj, dict) else []
        prefs = obj.get("prefs") if isinstance(obj, dict) else {}
        turns2 = [str(x) for x in (turns or []) if str(x).strip()]
        prefs2 = {str(k): str(v) for k, v in (prefs or {}).items() if str(k).strip() and str(v).strip()}
        return UserSession(turns=turns2, prefs=prefs2)
    except Exception:
        return UserSession(turns=[], prefs={})


async def save_session(
    r: redis.Redis,
    *,
    user_id: int,
    session: UserSession,
    ttl_seconds: int,
) -> None:
    await r.set(
        session_key(user_id),
        json.dumps({"turns": session.turns, "prefs": session.prefs, "ts": int(time.time())}, ensure_ascii=False),
        ex=ttl_seconds,
    )


async def append_turn(
    r: redis.Redis,
    *,
    user_id: int,
    query: str,
    max_turns: int,
    ttl_seconds: int,
) -> UserSession:
    s = await get_session(r, user_id=user_id)
    q = (query or "").strip()
    if q:
        s.turns.append(q)
    if max_turns > 0 and len(s.turns) > max_turns:
        s.turns = s.turns[-max_turns:]
    await save_session(r, user_id=user_id, session=s, ttl_seconds=ttl_seconds)
    return s


async def set_pref(
    r: redis.Redis,
    *,
    user_id: int,
    key: str,
    value: str,
    ttl_seconds: int,
) -> UserSession:
    s = await get_session(r, user_id=user_id)
    k = (key or "").strip()
    v = (value or "").strip()
    if k and v:
        s.prefs[k] = v
    await save_session(r, user_id=user_id, session=s, ttl_seconds=ttl_seconds)
    return s


def build_query_with_context(*, query: str, session: UserSession) -> str:
    q = (query or "").strip()
    if not q:
        return q

    ctx = ""
    if session.turns:
        prev = session.turns[-3:]
        ctx_lines = [f"- {x}" for x in prev if x.strip()]
        if ctx_lines:
            ctx += "\nКонтекст (последние запросы пользователя):\n" + "\n".join(ctx_lines) + "\n"

    prefs = ""
    if session.prefs:
        items = [f"{k}={v}" for k, v in session.prefs.items()]
        prefs = "\nПользовательские настройки:\n" + "\n".join([f"- {x}" for x in items]) + "\n"

    if not ctx and not prefs:
        return q

    return f"{q}\n{prefs}{ctx}".strip()