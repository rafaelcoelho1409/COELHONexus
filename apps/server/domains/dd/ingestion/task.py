"""Celery bridge for ingestion. Queued from POST /api/v1/docs-distiller/runs;
progress + manifest flow back through Redis."""
from __future__ import annotations
import infra.celery
from . import runtime

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name="domains.dd.ingestion.task.run_ingestion",
    bind=True,
    acks_late=False,
    track_started=True,
    soft_time_limit=3600,
    time_limit=3660,
)
def run_ingestion(self, run_id: str, slug: str) -> dict:
    """Run docs ingestion for `slug`; manifest lands at `coelhonexus:dd:runs:{run_id}:*`."""
    logger.info(f"[task] run_ingestion run_id={run_id} slug={slug}")
    try:
        return asyncio.run(runtime.dispatch.service.run(run_id, slug))
    except Exception as e:
        logger.exception(f"[task] run_ingestion failed: {e}")
        return {
            "run_id": run_id,
            "slug": slug,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }
