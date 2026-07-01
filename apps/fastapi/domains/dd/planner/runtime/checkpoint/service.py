"""AsyncPostgresSaver lifecycle; re-opens when the event loop changes because Celery prefork creates a new loop per task (stale saver → closed-pool OperationalError)."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ...keys import postgres_url


logger = logging.getLogger(__name__)


_saver: Optional[AsyncPostgresSaver] = None
_saver_ctx = None
_saver_loop: Optional[asyncio.AbstractEventLoop] = None


async def _create_target_database(url: str) -> None:
    """Bootstrap missing target DB; autocommit required because CREATE DATABASE cannot run inside a transaction."""
    parsed = urlparse(url)
    target_db = (parsed.path or "/").lstrip("/")
    if not target_db or target_db == "postgres":
        # Nothing to create; the URL already points at the admin DB.
        return
    # Rebuild the URL pointing at the admin DB; preserve user/pw/host/port.
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    logger.info(f"[checkpointer] bootstrapping missing DB '{target_db}'")
    async with await psycopg.AsyncConnection.connect(
        admin_url, autocommit = True
    ) as conn:
        # Quote the identifier defensively — CREATE DATABASE can't bind params.
        quoted = '"' + target_db.replace('"', '""') + '"'
        await conn.execute(f"CREATE DATABASE {quoted}")
    logger.info(f"[checkpointer] DB '{target_db}' created")


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

    # Bootstrap missing DB on first connect; recovery path is no-op in steady state.
    try:
        _saver_ctx = AsyncPostgresSaver.from_conn_string(url)
        _saver = await _saver_ctx.__aenter__()
    except psycopg.OperationalError as e:
        if "does not exist" not in str(e):
            raise
        await _create_target_database(url)
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
