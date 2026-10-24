"""Redis client factories.

The only module that imports `redis` or reads `REDIS_*` env vars. Everything
else reaches Redis dotted through here: `new_redis()` (async client the
caller owns and closes), `new_redis_sync()` (blocking client for sync code
paths), `url()` (broker URL for Celery), and the `Redis` / `Lock` classes for
annotations and distributed locks.

Clients are deliberately not cached: each caller opens one, uses it, and
closes it — FastAPI request handlers, Celery tasks (a fresh event loop per
task) and sync helpers all follow that lifecycle."""
from __future__ import annotations
from . import domain, params

import logging
from typing import Any

import redis as redis_sync
from redis.asyncio import Redis, from_url as _async_from_url
from redis.asyncio.lock import Lock


logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """True when `REDIS_HOST` is set."""
    return bool(params.HOST)


def url() -> str:
    """Connection URL built from the `REDIS_*` env vars."""
    if not params.HOST:
        raise RuntimeError("[redis] REDIS_HOST is not set")
    return domain.build_url(params.HOST, params.PORT, params.PASSWORD)


def new_redis(
    *, connect_timeout_s: float | None = params.CONNECT_TIMEOUT_S,
    op_timeout_s: float | None = params.OP_TIMEOUT_S, **kwargs: Any,
) -> Redis:
    """Fresh async client; the caller must `await aclose()` it."""
    return _async_from_url(
        url(), socket_connect_timeout = connect_timeout_s, socket_timeout = op_timeout_s, **kwargs,
    )


def new_redis_sync(
    *, connect_timeout_s: float | None = params.CONNECT_TIMEOUT_S,
    op_timeout_s: float | None = params.OP_TIMEOUT_S, **kwargs: Any,
) -> redis_sync.Redis:
    """Fresh blocking client; the caller must `close()` it."""
    return redis_sync.from_url(
        url(), socket_connect_timeout = connect_timeout_s, socket_timeout = op_timeout_s, **kwargs,
    )


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = ["Lock", "Redis", "is_configured", "new_redis", "new_redis_sync", "url"]
