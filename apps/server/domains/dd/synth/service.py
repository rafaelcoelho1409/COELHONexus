"""Synth orchestration glue shared by the Celery task shell — lock release + checkpointer bootstrap."""
from __future__ import annotations
import domains, infra
from . import keys

import logging


logger = logging.getLogger(__name__)


def _release_synth_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release of `dd:synth:lock:{slug}` from the task's finally block.
    Failure → stuck lock until TTL expiry; never raised."""
    if not slug or not thread_id:
        return
    try:
        r = infra.redis.service.new_redis_sync()
        try:
            r.eval(
                domains.dd.runtime.params.CAD_RELEASE_LUA,
                1, keys.lock_key(slug), thread_id,
            )
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[task] synth lock release failed for slug={slug!r}: "
            f"{type(e).__name__}: {e}"
        )


async def _init_and_run(coro):
    """Ensure the AsyncPostgresSaver is open in this worker's interpreter
    (idempotent module-scope cache), then run `coro`."""
    await infra.postgres.service.init_checkpointer()
    return await coro
