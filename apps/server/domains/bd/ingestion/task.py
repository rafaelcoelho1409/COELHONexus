"""Celery bridge for book extraction. Queued from POST /api/v1/bd/ingestion; progress flows back through the book's Redis event
stream and the manifest in MinIO. Not re-exported from the package: importing it needs the Celery app (REDIS_* and ENVIRONMENT)."""
from __future__ import annotations
import domains, infra.celery
from . import params

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = False,
    track_started = True,
    soft_time_limit = params.SOFT_TIME_LIMIT_S,
    time_limit = params.TIME_LIMIT_S,
)
def run_extraction(self, book_id: str, run_id: str, refresh: bool = False) -> dict:
    """Extract the pages of `book_id`; the manifest lands at `books/{book_id}/manifest.json`."""
    logger.info(f"[task] run_extraction book_id={book_id} run_id={run_id} refresh={refresh}")
    try:
        return asyncio.run(domains.bd.ingestion.runtime.dispatch.service.run(book_id, run_id, refresh))
    except Exception as e:  # the task's result carries the failure; the worker must not crash on it
        logger.exception("[task] run_extraction failed")
        return domains.bd.ingestion.runtime.dispatch.domain.result(
            book_id, run_id, "failed", 0, domains.bd.ingestion.runtime.dispatch.domain.error_text(e),
        )
