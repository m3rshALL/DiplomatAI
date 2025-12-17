from __future__ import annotations

import datetime as dt
import hashlib
import json
import traceback
from typing import Any

import redis.asyncio as redis


def create_redis(redis_url: str) -> redis.Redis:
    return redis.from_url(redis_url, decode_responses=True)


def _utc_day_key() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y%m%d")


def seconds_until_utc_day_end() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    tomorrow = (now + dt.timedelta(days=1)).date()
    end = dt.datetime.combine(tomorrow, dt.time(0, 0, 0), tzinfo=dt.timezone.utc)
    return max(60, int((end - now).total_seconds()))


def query_cache_key(query: str) -> str:
    norm = " ".join(query.strip().lower().split())
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    return f"perplexity:{h}"


def daily_limit_key(user_id: int) -> str:
    return f"limits:{user_id}:{_utc_day_key()}"

def error_events_key() -> str:
    return "admin:errors:recent"


async def push_error_event(
    r: redis.Redis,
    *,
    service: str,
    message: str,
    user_id: int | None = None,
    request_id: str | None = None,
    report_id: str | None = None,
    extra: dict[str, Any] | None = None,
    exc: BaseException | None = None,
    max_len: int = 500,
    ttl_seconds: int = 7 * 24 * 3600,
) -> None:
    payload: dict[str, Any] = {
        "ts": int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
        "service": service,
        "message": message,
    }
    if user_id is not None:
        payload["user_id"] = int(user_id)
    if request_id:
        payload["request_id"] = request_id
    if report_id:
        payload["report_id"] = report_id
    if extra:
        payload["extra"] = extra
    if exc is not None:
        payload["exc_type"] = exc.__class__.__name__
        payload["exc"] = str(exc)
        payload["traceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:]

    key = error_events_key()
    await r.lpush(key, json.dumps(payload, ensure_ascii=False, default=str))
    await r.ltrim(key, 0, max_len - 1)
    await r.expire(key, ttl_seconds)

async def cache_get_json(r: redis.Redis, key: str) -> dict | None:
    raw = await r.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def cache_set_json(r: redis.Redis, key: str, value: dict, ttl_seconds: int) -> None:
    await r.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl_seconds)


async def get_daily_used(r: redis.Redis, user_id: int) -> int:
    v = await r.get(daily_limit_key(user_id))
    try:
        return int(v or "0")
    except Exception:
        return 0


async def consume_daily_quota(r: redis.Redis, user_id: int) -> int:
    key = daily_limit_key(user_id)
    pipe = r.pipeline()
    pipe.incr(key, 1)
    pipe.expire(key, seconds_until_utc_day_end())
    res = await pipe.execute()
    return int(res[0])


