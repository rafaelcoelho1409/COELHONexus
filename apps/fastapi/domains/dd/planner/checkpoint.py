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
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


logger = logging.getLogger(__name__)


_saver: Optional[AsyncPostgresSaver] = None
_saver_ctx = None   # the async context manager that owns the connection pool


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
    """Open the connection pool and run .setup() once. Idempotent.

    Called from FastAPI lifespan. The returned saver is cached at module
    scope so subsequent graph compiles reuse the same pool.
    """
    global _saver, _saver_ctx
    if _saver is not None:
        return _saver

    url = _pg_url()
    logger.info(f"[checkpointer] connecting to {url.split('@')[-1]}")
    # AsyncPostgresSaver.from_conn_string is an async context manager —
    # we enter it once at startup and exit it at shutdown via close().
    _saver_ctx = AsyncPostgresSaver.from_conn_string(url)
    _saver = await _saver_ctx.__aenter__()
    await _saver.setup()
    logger.info("[checkpointer] AsyncPostgresSaver ready (setup() idempotent)")
    return _saver


async def close_checkpointer() -> None:
    """Tear down the connection pool. Called from lifespan shutdown."""
    global _saver, _saver_ctx
    if _saver_ctx is None:
        return
    try:
        await _saver_ctx.__aexit__(None, None, None)
    except Exception as e:
        logger.warning(f"[checkpointer] shutdown error (non-fatal): {e}")
    _saver = None
    _saver_ctx = None


def get_checkpointer() -> AsyncPostgresSaver:
    """Synchronous accessor — must be called after init_checkpointer()
    has resolved. Raises if accessed too early."""
    if _saver is None:
        raise RuntimeError(
            "AsyncPostgresSaver not initialized — call init_checkpointer() "
            "from FastAPI lifespan before any graph.compile()"
        )
    return _saver
