"""Celery worker for post-publication Book Distiller retrieval indexing."""
from __future__ import annotations

import asyncio
import logging

import infra.celery

from . import params, service


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
)
def index_distill_run(self, book_id: str, run_id: str) -> dict:
    logger.info("[bd-distill-rag-task] book_id=%s run_id=%s", book_id, run_id)
    result = asyncio.run(service.run_index(book_id, run_id))
    return {
        "book_id": book_id,
        "run_id": run_id,
        "status": result["status"] if result else "missing",
        "point_count": result["point_count"] if result else 0,
    }
