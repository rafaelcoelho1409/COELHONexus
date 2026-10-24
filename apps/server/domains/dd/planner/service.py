"""Planner orchestration glue shared by the Celery task shell — lock release + checkpointer bootstrap + the plan blob reader."""
from __future__ import annotations
import domains
from . import keys, params

import json
import logging

import redis as redis_sync


logger = logging.getLogger(__name__)


async def load_plan(slug: str) -> dict | None:
    """The latest finalized plan blob (chapter outline + sources) for `slug`,
    or None when it does not exist yet. Raises when the blob exists but is
    unreadable/corrupt — the caller maps that onto its own error surface.

    Single reader for `planner/{slug}/plan-latest.json`: the planner writes
    it (plan_write node), the synth router and the outline node read it."""
    minio = domains.dd.ingestion.storage.service.get_storage()
    key = keys.plan_latest_key(slug)
    if not await minio.exists(key):
        return None
    return json.loads(await minio.read_text(key)) or {}


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
            r.eval(
                domains.dd.runtime.params.CAD_RELEASE_LUA,
                1, keys.lock_key(slug), thread_id,
            )
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
