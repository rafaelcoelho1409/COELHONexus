"""Celery task: docs distiller planner.

Bridges Celery's sync execution model to the async LangGraph planner.
Queued from `POST /api/v1/docs-distiller/planner/{slug}` and `/resume`;
the worker picks tasks up from the `planner-{env}` queue, runs the full
8-node graph, and persists every checkpoint to Postgres via
AsyncPostgresSaver. The FastAPI SSE endpoint subscribes to the Redis
pub/sub channel the worker publishes to, so live progress streams to the
UI from the worker process.

Two tasks:
  - run_planner(thread_id, slug, mode):
        fresh kickoff; delegates to dispatch.run_planner_async.
  - resume_planner(thread_id):
        resume from last checkpoint; handles standard resume + catch-up
        for missing IMPLEMENTED nodes inside dispatch.resume_planner_async.

Both follow the canonical pattern from `domains/dd/ingestion/task.py`:
  asyncio.run() bridge + dict return + try/except → "failed" envelope.
"""
import asyncio
import logging
import os

import redis as redis_sync

from celery_app import app

from .checkpoint import init_checkpointer
from .dispatch import resume_planner_async, run_planner_async


logger = logging.getLogger(__name__)


# Compare-and-delete Lua script — DELETE the lock only if its value still
# matches the thread_id we're releasing for. Prevents a slow finally from
# accidentally clearing a lock held by a later, racing start of the same
# slug (when the original lock's TTL has expired and a fresh start has
# acquired in between).
_CAD_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)


def _slug_from_planner_thread_id(thread_id: str) -> str | None:
    """Planner thread_id format is `docs-distiller/{slug}/{uuid}` —
    extract the slug for the lock-release call. Returns None if the
    format doesn't match (legacy thread, malformed input)."""
    parts = (thread_id or "").split("/", 2)
    if len(parts) >= 2 and parts[0] == "docs-distiller":
        return parts[1] or None
    return None


def _release_planner_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release of `dd:planner:lock:{slug}` from
    the task's finally block. Sync because the task wrapper itself is
    sync; spinning up an asyncio loop just to delete one key is wasteful.
    Failure here is logged but never raised — a stuck lock just delays
    the next planner of this slug until the TTL expires."""
    if not slug or not thread_id:
        return
    try:
        host = os.environ.get(
            "REDIS_HOST", "redis-master.redis.svc.cluster.local",
        )
        port = os.environ.get("REDIS_PORT", "6379")
        pwd = os.environ.get("REDIS_PASSWORD", "")
        url = (
            f"redis://:{pwd}@{host}:{port}" if pwd
            else f"redis://{host}:{port}"
        )
        r = redis_sync.from_url(
            url, socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            r.eval(
                _CAD_RELEASE_LUA, 1,
                f"dd:planner:lock:{slug}", thread_id,
            )
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[task] planner lock release failed for slug={slug!r}: "
            f"{type(e).__name__}: {e}"
        )


async def _init_and_run(coro):
    """Per-task: ensure the AsyncPostgresSaver is open in THIS worker
    process's interpreter (idempotent — module-scope cache guards), then
    run the requested coroutine. The init is async because
    AsyncPostgresSaver.from_conn_string returns an async context manager;
    each worker process opens its own connection pool once and reuses it
    across all tasks in that process."""
    await init_checkpointer()
    return await coro


@app.task(
    name="domains.dd.planner.task.run_planner",
    bind=True,
    acks_late=False,
    track_started=True,
    # Planner empirically takes 12-15min on LangChain-scale corpora (777 docs);
    # 60min soft / 65min hard gives 4× headroom for cold embed_corpus + bandit
    # warm-up + heavy off_topic. Same shape as ingestion (3600/3660).
    soft_time_limit=3600,
    time_limit=3660,
)
def run_planner(self, thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Run a fresh planner pass for `slug`. The graph emits progress
    events via Redis pub/sub (channel `dd:planner:{thread_id}:events`)
    that the FastAPI SSE endpoint streams to the UI.

    The outer try/finally releases the global single-flight lock
    (`dd:planner:lock:{slug}`) acquired by POST /planner/{slug}, no
    matter how the task terminates — success, exception, or cancel.
    CAD-release (compare-and-delete) is used so a slow finally never
    clears a lock that a later, racing start has already taken."""
    logger.info(
        f"[task] run_planner thread_id={thread_id} slug={slug} mode={mode}"
    )
    try:
        try:
            return asyncio.run(
                _init_and_run(run_planner_async(thread_id, slug, mode))
            )
        except Exception as e:
            logger.exception(f"[task] run_planner failed: {e}")
            return {
                "thread_id": thread_id,
                "slug": slug,
                "mode": mode,
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
    finally:
        _release_planner_lock(slug, thread_id)


@app.task(
    name="domains.dd.planner.task.resume_planner",
    bind=True,
    acks_late=False,
    track_started=True,
    soft_time_limit=3600,
    time_limit=3660,
)
def resume_planner(self, thread_id: str) -> dict:
    """Resume a planner run from its last LangGraph checkpoint. Handles
    standard ainvoke(None) resume + catch-up (missing newly-implemented
    nodes) internally.

    Resume doesn't acquire the single-flight lock — it picks up where a
    dead task left off. But it DOES CAD-release on completion so that if
    the original task's finally never fired (worker SIGKILL), the lock
    isn't held until its TTL expires. CAD ensures we don't accidentally
    release a lock taken by a later, racing fresh start."""
    logger.info(f"[task] resume_planner thread_id={thread_id}")
    slug = _slug_from_planner_thread_id(thread_id)
    try:
        try:
            return asyncio.run(
                _init_and_run(resume_planner_async(thread_id))
            )
        except Exception as e:
            logger.exception(f"[task] resume_planner failed: {e}")
            return {
                "thread_id": thread_id,
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
    finally:
        if slug:
            _release_planner_lock(slug, thread_id)
