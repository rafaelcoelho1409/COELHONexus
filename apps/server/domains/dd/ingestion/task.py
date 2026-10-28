"""Celery bridge for ingestion. Queued from POST /api/v1/dd/runs;
progress + manifest flow back through Redis."""
from __future__ import annotations
import infra.celery
from . import params, runtime

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name=params.TASK_NAME,
    bind=True,
    acks_late=False,
    track_started=True,
    soft_time_limit=params.SOFT_TIME_LIMIT_S,
    time_limit=params.TIME_LIMIT_S,
)
def run_ingestion(self, run_id: str, slug: str) -> dict:
    """Run docs ingestion for `slug`; manifest lands at `coelhonexus:dd:runs:{run_id}:*`."""
    logger.info(f"[task] run_ingestion run_id={run_id} slug={slug}")
    try:
        return asyncio.run(runtime.dispatch.service.run(run_id, slug))
    except Exception as e:  # the task's result carries the failure; the worker must not crash on it
        logger.exception("[task] run_ingestion failed")
        return runtime.dispatch.domain.rejected_result(run_id, slug, runtime.dispatch.domain.error_text(e))
