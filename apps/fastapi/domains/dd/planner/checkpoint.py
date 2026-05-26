"""AsyncPostgresSaver factory.

Single shared checkpointer instance per FastAPI process (the connection
pool inside it is the expensive bit; reuse it). Built lazily on first
graph compile so unit tests / local dev can import the planner module
without Postgres being up.

  POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD /
  POSTGRES_DATABASE   — already mounted via coelhonexus-secret

The `.setup()` call is idempotent — safe to invoke on every startup.
Creates the `checkpoints`, `checkpoint_writes`, and `checkpoint_blobs`
tables on first run, no-op afterwards.

2026-05-26 — event-loop-aware caching (Bundle 13 follow-up).
psycopg's async pool is tied to the asyncio event loop that opened it. In
Celery prefork workers, every task runs inside its own `asyncio.run(...)`
(fresh loop per task), but the module-cached `_saver` was sticky across
tasks → second task on the same worker process used a saver from a
closed loop → `psycopg.OperationalError: the connection is closed` on
the first checkpoint write. `init_checkpointer()` now records the loop
that opened the saver and forces a fresh open whenever the running loop
differs (which is exactly once per Celery task, never per FastAPI request).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


logger = logging.getLogger(__name__)


_saver: Optional[AsyncPostgresSaver] = None
_saver_ctx = None   # the async context manager that owns the connection pool
_saver_loop: Optional[asyncio.AbstractEventLoop] = None  # loop that opened _saver


def _pg_url() -> str:
    """Build the Postgres URL. User + password are percent-encoded —
    POSTGRES_PASSWORD may contain `%`, `&`, `#`, `!`, `^` which would
    otherwise break URL parsing (`invalid percent-encoded token`)."""
    user = quote(os.environ.get("POSTGRES_USER", "postgres"), safe="")
    password = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "postgresql.postgresql.svc.cluster.local")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DATABASE", "postgres")
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


async def init_checkpointer() -> AsyncPostgresSaver:
    """Open the connection pool and run .setup(). Idempotent within an
    event loop; re-opens when the running loop changes (Celery tasks).

    Called from FastAPI lifespan AND from Celery task entry points
    (see domains/dd/{planner,synth}/task.py::_init_and_run). In FastAPI
    the running loop never changes across requests, so this returns the
    cached saver immediately. In Celery prefork workers each task uses a
    fresh `asyncio.run(...)`, so the loop check forces a re-open per task
    — the prior loop's pool is unreachable anyway (its event loop is
    closed) and gets reclaimed by GC.
    """
    global _saver, _saver_ctx, _saver_loop
    current_loop = asyncio.get_running_loop()
    if _saver is not None and _saver_loop is current_loop:
        return _saver

    if _saver is not None and _saver_loop is not current_loop:
        # Loop change → cached saver is bound to a closed loop. Drop the
        # references (GC will reclaim; we cannot call __aexit__ from the
        # new loop because the context manager is tied to the old one).
        logger.info(
            "[checkpointer] event loop changed; dropping stale saver "
            "and re-opening on the current loop"
        )
        _saver = None
        _saver_ctx = None
        _saver_loop = None

    url = _pg_url()
    logger.info(f"[checkpointer] connecting to {url.split('@')[-1]}")
    # AsyncPostgresSaver.from_conn_string is an async context manager —
    # we enter it once per loop and rely on loop teardown to clean up
    # (Celery tasks) or our close() helper (FastAPI shutdown).
    _saver_ctx = AsyncPostgresSaver.from_conn_string(url)
    _saver = await _saver_ctx.__aenter__()
    await _saver.setup()
    _saver_loop = current_loop
    logger.info("[checkpointer] AsyncPostgresSaver ready (setup() idempotent)")
    return _saver


async def close_checkpointer() -> None:
    """Tear down the connection pool. Called from lifespan shutdown."""
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
    """Synchronous accessor — must be called after init_checkpointer()
    has resolved. Raises if accessed too early."""
    if _saver is None:
        raise RuntimeError(
            "AsyncPostgresSaver not initialized — call init_checkpointer() "
            "from FastAPI lifespan before any graph.compile()"
        )
    return _saver
