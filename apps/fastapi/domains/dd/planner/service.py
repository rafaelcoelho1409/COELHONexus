"""Planner orchestration glue shared by the Celery task shell — lock release + checkpointer bootstrap."""
from __future__ import annotations
import domains
from . import keys, params

import logging

import redis as redis_sync


logger = logging.getLogger(__name__)


# Compare-and-delete — release only if the lock still holds OUR thread_id.
# Stops a slow finally from clearing a lock a racing start has taken.
_CAD_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)


def _release_planner_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release from the task's finally block.
    Failure → stuck lock until TTL expiry; never raised."""
    if not slug or not thread_id:
        return
    try:
        r = redis_sync.from_url(
            keys.redis_url(),
            socket_connect_timeout = params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout = params.REDIS_OP_TIMEOUT_S,
        )
        try:
            r.eval(_CAD_RELEASE_LUA, 1, keys.lock_key(slug), thread_id)
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
    await domains.dd.planner.runtime.checkpoint.service.init_checkpointer()
    return await coro
