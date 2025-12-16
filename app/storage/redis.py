from __future__ import annotations

import datetime as dt
import hashlib
import json

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


