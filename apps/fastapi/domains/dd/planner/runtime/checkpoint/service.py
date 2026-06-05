"""AsyncPostgresSaver lifecycle. Event-loop-aware cache: psycopg's async pool
is bound to the loop that opened it; Celery prefork workers run each task
under a fresh `asyncio.run(...)` (new loop per task), so a sticky cached
saver was using a closed loop → `OperationalError: connection is closed`.
We re-open whenever the running loop differs (once per Celery task; never
per FastAPI request).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ...keys import postgres_url


logger = logging.getLogger(__name__)


_saver: Optional[AsyncPostgresSaver] = None
_saver_ctx = None
_saver_loop: Optional[asyncio.AbstractEventLoop] = None


async def init_checkpointer() -> AsyncPostgresSaver:
    """Open the pool and run `.setup()`. Idempotent within an event loop;
    re-opens when the running loop changes (Celery tasks)."""
    global _saver, _saver_ctx, _saver_loop
    current_loop = asyncio.get_running_loop()
    if _saver is not None and _saver_loop is current_loop:
        return _saver

    if _saver is not None and _saver_loop is not current_loop:
        logger.info(
            "[checkpointer] event loop changed; dropping stale saver "
            "and re-opening on the current loop"
        )
        _saver = None
        _saver_ctx = None
        _saver_loop = None

    url = postgres_url()
    logger.info(f"[checkpointer] connecting to {url.split('@')[-1]}")
    _saver_ctx = AsyncPostgresSaver.from_conn_string(url)
    _saver = await _saver_ctx.__aenter__()
    await _saver.setup()
    _saver_loop = current_loop
    logger.info("[checkpointer] AsyncPostgresSaver ready (setup() idempotent)")
    return _saver


async def close_checkpointer() -> None:
    """Tear down the pool. Called from lifespan shutdown."""
    global _saver, _saver_ctx, _saver_loop
    if _saver_ctx is None:
        return
    try:
        await _saver_ctx.__aexit__(None, None, None)
    except Exception as e:
        logger.warning(f"[checkpointer] shutdown error (non-fatal): {e}")
    _saver = None
    _saver_ctx = None
    _saver_loop = None


def get_checkpointer() -> AsyncPostgresSaver:
    """Sync accessor — must be called after `init_checkpointer()` resolved."""
    if _saver is None:
        raise RuntimeError(
            "AsyncPostgresSaver not initialized — call init_checkpointer() "
            "from FastAPI lifespan before any graph.compile()"
        )
    return _saver
