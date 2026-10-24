"""Async Qdrant client factory + lifecycle.

Lives at the infra layer (not under any one domain) because multiple
domains will share a single cluster client — YCS holds video chunks
under one collection; future DD code may add a separate collection.

PER-LOOP CACHING — same Celery-prefork reasoning as infra/neo4j/service.py.
`AsyncQdrantClient` wraps an httpx.AsyncClient whose connection pool is
loop-bound; reusing it across `asyncio.run()` boundaries surfaces as
`RuntimeError: ... attached to a different loop`. The WeakKeyDictionary
keyed on the running loop gives FastAPI one client for life (1 loop) AND
Celery one client per task (auto-evicted when the task's loop is GC'd).

The only module that imports `qdrant_client` / `langchain_qdrant` or reads
`QDRANT_*` env vars. Everything else reaches Qdrant dotted through here:
`get_qdrant()` (per-loop shared client), `new_qdrant()` (a client the caller
owns and closes, for Celery tasks that manage their own lifecycle), the
`models` module (request / query value objects such as `models.Filter`),
and the `AsyncQdrantClient` / `FastEmbedSparse` classes."""
from __future__ import annotations
import infra
from . import params

import asyncio
import logging
import weakref

from langchain_qdrant import FastEmbedSparse
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models


logger = logging.getLogger(__name__)

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncQdrantClient]" = (
    weakref.WeakKeyDictionary()
)


def new_qdrant() -> AsyncQdrantClient:
    """Fresh client owned by the caller, who must `await close()` it."""
    return AsyncQdrantClient(
        host    = params.QDRANT_HOST,
        port    = params.QDRANT_PORT,
        api_key = params.QDRANT_API_KEY,
        https   = params.QDRANT_HTTPS,
        timeout = params.TIMEOUT_S,
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
        client = new_qdrant()
        _clients[loop] = client
        logger.info(
            f"[qdrant] client init {params.QDRANT_HOST}:{params.QDRANT_PORT} "
            f"https={params.QDRANT_HTTPS} (loop={id(loop):x})"
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


def span(operation: str, **attrs):
    """`db.qdrant.<operation>` client span around a Qdrant call; `attrs` are extra span
    attributes (`db.qdrant.collection_name`, ...)."""
    return infra.opentelemetry.service.db_span("qdrant", operation, **attrs)


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = [
    "AsyncQdrantClient", "FastEmbedSparse", "close_qdrant", "get_qdrant", "models", "new_qdrant", "span",
]
