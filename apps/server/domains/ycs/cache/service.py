"""ycs/cache — async Redis cache for full RAG responses.

Imperative Shell — wraps `redis.asyncio.Redis` with read / write /
invalidate primitives. Caller passes the redis client (consistent
with the deprecated signature; no module-level singleton).
"""
from __future__ import annotations
from . import keys, params

import json
import logging
import time

import redis.asyncio as redis_aio


logger = logging.getLogger(__name__)


async def get_cached_response(
    redis: redis_aio.Redis,
    question: str,
    mode: str | None = None,
) -> dict | None:
    """Return the cached payload or None. Best-effort — Redis hiccups
    surface as a cache miss, never a 5xx for the caller."""
    key = keys.cache_key(question, mode)
    try:
        raw = await redis.get(key)
    except Exception as e:
        logger.warning(f"[ycs:cache] get failed: {type(e).__name__}: {e}")
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def cache_response(
    redis: redis_aio.Redis,
    question: str,
    response: dict,
    ttl: int = params.DEFAULT_TTL_S,
    mode: str | None = None,
) -> None:
    """Persist the response with TTL. Best-effort. `_cached_at` is
    stamped on the payload so the consumer can surface cache age."""
    key = keys.cache_key(question, mode)
    payload = {**response, "_cached_at": time.time()}
    try:
        await redis.set(key, json.dumps(payload, ensure_ascii = False), ex = ttl)
    except Exception as e:
        logger.warning(f"[ycs:cache] set failed: {type(e).__name__}: {e}")


async def invalidate_cache(
    redis: redis_aio.Redis,
    question: str | None = None,
) -> int:
    """If `question` is supplied, drop that one key. If None, scan +
    drop every key under `CACHE_PREFIX`. Returns the count cleared."""
    cleared = 0
    try:
        if question is not None:
            key = keys.cache_key(question)
            n = await redis.delete(key)
            cleared = int(n or 0)
        else:
            async for key in redis.scan_iter(match = f"{params.CACHE_PREFIX}*"):
                try:
                    await redis.delete(key)
                    cleared += 1
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[ycs:cache] invalidate failed: {type(e).__name__}: {e}")
    return cleared
