"""Celery bridge to the async LangGraph planner.

Queued from `POST /api/v1/docs-distiller/planner/{slug}` and `/resume`;
worker runs the full graph and persists checkpoints to Postgres via
AsyncPostgresSaver. Progress streams over Redis pub/sub from worker.
Same shape as `domains/dd/ingestion/task.py`: asyncio.run bridge + dict
return + try/except → "failed" envelope.
"""
import asyncio
import logging

import redis as redis_sync

from infra.celery import app

from .runtime.checkpoint import init_checkpointer
from .runtime.dispatch import resume_planner_async, run_planner_async
from .keys import lock_key, redis_url
from .params import REDIS_CONNECT_TIMEOUT_S, REDIS_OP_TIMEOUT_S


logger = logging.getLogger(__name__)


# Compare-and-delete — release only if the lock still holds OUR thread_id.
# Stops a slow finally from clearing a lock a racing start has taken.
_CAD_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)


def _slug_from_planner_thread_id(thread_id: str) -> str | None:
    """`docs-distiller/{slug}/{uuid}` → slug. None on format mismatch."""
    parts = (thread_id or "").split("/", 2)
    if len(parts) >= 2 and parts[0] == "docs-distiller":
        return parts[1] or None
    return None


def _release_planner_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release from the task's finally block.
    Failure → stuck lock until TTL expiry; never raised."""
    if not slug or not thread_id:
        return
    try:
        r = redis_sync.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout = REDIS_OP_TIMEOUT_S,
        )
        try:
            r.eval(_CAD_RELEASE_LUA, 1, lock_key(slug), thread_id)
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[task] planner lock release failed for slug={slug!r}: "
            f"{type(e).__name__}: {e}"
        )


async def _init_and_run(coro):
    """Ensure the AsyncPostgresSaver is open in this worker's interpreter
    (idempotent module-scope cache), then run `coro`."""
    await init_checkpointer()
    return await coro


@app.task(
    name = "domains.dd.planner.task.run_planner",
    bind = True,
    acks_late = False,
    track_started = True,
    # 4× headroom over the ~12-15 min observed on LangChain-scale (777 docs).
    soft_time_limit = 3600,
    time_limit = 3660,
)
def run_planner(self, thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Fresh planner pass. SSE events flow over Redis pub/sub channel
    `dd:planner:{thread_id}:events`. Outer try/finally CAD-releases the
    `dd:planner:lock:{slug}` single-flight lock regardless of outcome."""
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
    name = "domains.dd.planner.task.resume_planner",
    bind = True,
    acks_late = False,
    track_started = True,
    soft_time_limit = 3600,
    time_limit = 3660,
)
def resume_planner(self, thread_id: str) -> dict:
    """Resume from last checkpoint. Standard ainvoke(None) + catch-up
    (missing newly-implemented nodes) handled inline. Doesn't acquire
    the single-flight lock — picks up where a dead task left off — but
    DOES CAD-release on completion (covers SIGKILL'd original tasks)."""
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
