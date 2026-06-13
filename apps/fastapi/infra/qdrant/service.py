"""Async Qdrant client factory + lifecycle.

Lives at the infra layer (not under any one domain) because multiple
domains will share a single cluster client — YCS holds video chunks
under one collection; future DD code may add a separate collection.

PER-LOOP CACHING — same Celery-prefork reasoning as infra/neo4j/service.py.
`AsyncQdrantClient` wraps an httpx.AsyncClient whose connection pool is
loop-bound; reusing it across `asyncio.run()` boundaries surfaces as
`RuntimeError: ... attached to a different loop`. The WeakKeyDictionary
keyed on the running loop gives FastAPI one client for life (1 loop) AND
Celery one client per task (auto-evicted when the task's loop is GC'd)."""
from __future__ import annotations

import asyncio
import logging
import weakref

from qdrant_client import AsyncQdrantClient

from .params import (
    QDRANT_API_KEY,
    QDRANT_HOST,
    QDRANT_HTTPS,
    QDRANT_PORT,
    TIMEOUT_S,
)


logger = logging.getLogger(__name__)

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncQdrantClient]" = (
    weakref.WeakKeyDictionary()
)


def _make_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(
        host    = QDRANT_HOST,
        port    = QDRANT_PORT,
        api_key = QDRANT_API_KEY,
        https   = QDRANT_HTTPS,
        timeout = TIMEOUT_S,
    )


def get_qdrant() -> AsyncQdrantClient:
    """Return the AsyncQdrantClient bound to the currently-running event loop.

    Must be called from inside an async context. The cache is per-loop so
    Celery tasks (each running on a fresh `asyncio.run()` loop) don't reuse
    a doomed client created against a prior, now-closed loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            "[qdrant] get_qdrant() must be called from inside an async context "
            "(no running event loop)"
        )
    client = _clients.get(loop)
    if client is None:
        client = _make_client()
        _clients[loop] = client
        logger.info(
            f"[qdrant] client init {QDRANT_HOST}:{QDRANT_PORT} "
            f"https={QDRANT_HTTPS} (loop={id(loop):x})"
        )
    return client


async def close_qdrant() -> None:
    """Close THIS event loop's client. Idempotent. Each Celery task should
    call this in its `finally` block to release the httpx connection pool
    before the loop dies."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    client = _clients.pop(loop, None)
    if client is not None:
        try:
            await client.close()
        except Exception as e:
            logger.warning(f"[qdrant] close failed: {e}")
