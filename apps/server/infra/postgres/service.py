"""Postgres connection factory + the shared AsyncPostgresSaver lifecycle.

The only module that imports `psycopg` / `psycopg_pool` /
`langgraph.checkpoint.postgres` or reads the `POSTGRES_*` env vars. Everything
else reaches Postgres dotted through here: `connect()` (awaitable async
connection the caller closes), `url()` (connection URL, for the few places that
need a DSN string), `init_checkpointer()` / `get_checkpointer()` /
`close_checkpointer()` (the LangGraph saver shared by planner + synth), and the
`Jsonb` / `AsyncPostgresSaver` names for query parameters and annotations.

Connections are deliberately not cached: each operation opens one, uses it and
closes it (the checkpointer owns its own pool)."""
from __future__ import annotations
from . import domain, params

import asyncio
import logging
from typing import Optional

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


logger = logging.getLogger(__name__)


def check_configured() -> None:
    """Raise one error naming every missing Postgres env var."""
    missing = [
        name for name, value in (
            ("POSTGRES_HOST", params.HOST),
            ("POSTGRES_USER", params.USER),
            ("POSTGRES_DATABASE", params.DATABASE),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"[postgres] missing env: {', '.join(missing)}")


def is_configured() -> bool:
    """True when the host, user and default database are set."""
    return bool(params.HOST and params.USER and params.DATABASE)


def url() -> str:
    """Connection URL built from the `POSTGRES_*` env vars."""
    check_configured()
    return domain.build_url(
        params.USER, params.PASSWORD, params.HOST, params.PORT, params.DATABASE,
    )


def connect(dsn: str | None = None, **kwargs):
    """Awaitable async connection to `dsn` (default: `url()`); `kwargs` go to psycopg. Use:

        async with await infra.postgres.service.connect() as conn:
            await conn.execute(...)
    """
    return psycopg.AsyncConnection.connect(dsn or url(), **kwargs)


# ---------------------------------------------------------------------------
# LangGraph checkpointer — shared by planner + synth
# ---------------------------------------------------------------------------

_saver: Optional[AsyncPostgresSaver] = None
_pool: Optional[AsyncConnectionPool] = None
_saver_loop: Optional[asyncio.AbstractEventLoop] = None


async def _create_target_database(db_url: str) -> None:
    """Bootstrap missing target DB; autocommit required because CREATE DATABASE cannot run inside a transaction."""
    target_db = domain.target_database(db_url)
    if not target_db or target_db == "postgres":
        # Nothing to create; the URL already points at the admin DB.
        return
    # Rebuild the URL pointing at the admin DB; preserve user/pw/host/port.
    logger.info(f"[checkpointer] bootstrapping missing DB '{target_db}'")
    async with await connect(domain.admin_url(db_url), autocommit = True) as conn:
        await conn.execute(f"CREATE DATABASE {domain.quote_identifier(target_db)}")
    logger.info(f"[checkpointer] DB '{target_db}' created")


async def init_checkpointer() -> AsyncPostgresSaver:
    """Open the pool and run `.setup()`. Idempotent within an event loop;
    re-opens when the running loop changes (Celery tasks — prefork creates a new
    loop per task, and a stale saver raises closed-pool OperationalError).

    Backed by an `AsyncConnectionPool` with `check_connection` on checkout,
    NOT `AsyncPostgresSaver.from_conn_string` (one long-lived
    `AsyncConnection`): in the long-lived FastAPI process that single
    connection died after a Postgres/network drop and was never reopened, so
    every `aget_state` raised "the connection is closed" (planner `/state`
    polling 500'd for the whole pod lifetime). The pool discards dead
    connections and reconnects transparently."""
    global _saver, _pool, _saver_loop
    current_loop = asyncio.get_running_loop()
    if _saver is not None and _saver_loop is current_loop:
        return _saver

    if _saver is not None and _saver_loop is not current_loop:
        logger.info(
            "[checkpointer] event loop changed; dropping stale saver "
            "and re-opening on the current loop"
        )
        _saver = None
        _pool = None
        _saver_loop = None

    db_url = url()
    logger.info(f"[checkpointer] connecting to {db_url.split('@')[-1]}")

    # Bootstrap missing DB on first connect; recovery path is no-op in steady
    # state. Probed with a throwaway connection because a pool's open() times
    # out (PoolTimeout) instead of surfacing "database does not exist".
    try:
        async with await connect(db_url):
            pass
    except psycopg.OperationalError as e:
        if "does not exist" not in str(e):
            raise
        await _create_target_database(db_url)

    # Same connection kwargs `AsyncPostgresSaver.from_conn_string` uses.
    _pool = AsyncConnectionPool(
        conninfo = db_url,
        min_size = params.POOL_MIN_SIZE,
        max_size = params.POOL_MAX_SIZE,
        kwargs = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check = AsyncConnectionPool.check_connection,
        open = False,
    )
    await _pool.open(wait = True, timeout = params.POOL_OPEN_TIMEOUT_S)
    _saver = AsyncPostgresSaver(_pool)

    await _saver.setup()
    _saver_loop = current_loop
    logger.info("[checkpointer] AsyncPostgresSaver ready (setup() idempotent)")
    return _saver


async def close_checkpointer() -> None:
    """Tear down the pool. Called from lifespan shutdown."""
    global _saver, _pool, _saver_loop
    if _pool is None:
        return
    try:
        await _pool.close()
    except Exception as e:
        logger.warning(f"[checkpointer] shutdown error (non-fatal): {e}")
    _saver = None
    _pool = None
    _saver_loop = None


def get_checkpointer() -> AsyncPostgresSaver:
    """Sync accessor — must be called after `init_checkpointer()` resolved."""
    if _saver is None:
        raise RuntimeError(
            "AsyncPostgresSaver not initialized — call init_checkpointer() "
            "from FastAPI lifespan before any graph.compile()"
        )
    return _saver


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = [
    "AsyncPostgresSaver", "Jsonb", "check_configured", "close_checkpointer", "connect",
    "get_checkpointer", "init_checkpointer", "is_configured", "url",
]
