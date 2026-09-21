"""ycs/embedding_migration — the tiny cutover task.

The actual re-embed work reuses `domains.ycs.qdrant_task.task
.ingest_to_qdrant` unchanged (it already accepts `video_ids=None` for
"every ES transcript" and now takes an optional `collection_name`
override) — no need to duplicate chunk/embed/upsert logic. This module
only adds what that task doesn't do: atomically re-pointing the
`QDRANT_COLLECTION` alias once the re-embed succeeds.

Wired via `Signature.link()`, not `celery.chain()` — a chain's overall
`AsyncResult.id` refers to the LAST task, which shows PENDING for the
entire (long) re-embed phase; `.link()` on the re-embed signature keeps
that signature's OWN task id as the one callers poll, so live progress
(`_progress` callback → `self.update_state`) is visible immediately, same
as every other YCS Celery task. See `service.dispatch_migration` for the
wiring. If the re-embed task fails, `.link()` never invokes this
finalize step — migration state stays `status: "running"` (stale but
SAFE: `check_migration_needed` reads live Qdrant data, not this status
field, so the dispatch gate stays correctly closed) and the task's own
FAILURE state is what the poller/UI surfaces."""
from __future__ import annotations

import asyncio
import os

from celery.utils.log import get_task_logger
from qdrant_client import AsyncQdrantClient

import infra.celery.service


logger = get_task_logger(__name__)


@infra.celery.service.app.task(
    bind = True,
    name = "domains.ycs.embedding_migration.task.finalize_embedding_migration",
)
def finalize_embedding_migration(self, physical_collection: str) -> dict:
    """Re-point the `QDRANT_COLLECTION` alias at `physical_collection`
    and clear migration state. Runs only after the re-embed task
    (linked via `.link()`) succeeds."""
    logger.info(f"[finalize_embedding_migration] cutover -> {physical_collection!r}")

    async def _run() -> dict:
        import redis.asyncio as redis_aio
        from . import service

        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = os.environ.get("REDIS_PORT", "6379")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        redis_url = (
            f"redis://:{redis_password}@{redis_host}:{redis_port}"
            if redis_password else f"redis://{redis_host}:{redis_port}"
        )
        redis = redis_aio.from_url(redis_url)
        qdrant_api_key = os.environ.get("QDRANT_API_KEY")
        qdrant = AsyncQdrantClient(
            url     = os.environ.get("QDRANT_URL", "http://localhost:6333"),
            port    = int(os.environ.get("QDRANT_PORT", "6333")),
            api_key = qdrant_api_key if qdrant_api_key else None,
        )
        try:
            await service.cutover(redis, qdrant, physical_collection)
            return {"status": "done", "collection": physical_collection}
        finally:
            await qdrant.close()
            await redis.aclose()

    result = asyncio.run(_run())
    logger.info(f"[finalize_embedding_migration] Done: {result}")
    return result
