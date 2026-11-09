"""Celery worker for the book-scoped retrieval index."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import infra
import infra.celery

from . import params, service


logger = logging.getLogger(__name__)


async def _run_index(book_id: str, run_id: str) -> dict[str, Any] | None:
    try:
        return await service.run_index(book_id, run_id)
    finally:
        await infra.neo4j.service.close_neo4j()


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
)
def index_book(self, book_id: str, run_id: str) -> dict:
    logger.info("[bd-rag-task] book_id=%s run_id=%s", book_id, run_id)
    result = asyncio.run(_run_index(book_id, run_id))
    return {
        "book_id": book_id,
        "distill_run_id": run_id,
        "status": result["status"] if result else "missing",
        "point_count": result["point_count"] if result else 0,
    }


@infra.celery.service.app.task(
    name = "domains.bd.distill.rag.task.index_distill_run",
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
)
def index_distill_run_compat(self, book_id: str, run_id: str) -> dict:
    """Accept a retrieval job queued by a worker from the previous module path."""
    logger.info("[bd-rag-compat-task] book_id=%s run_id=%s", book_id, run_id)
    result = asyncio.run(_run_index(book_id, run_id))
    return {
        "book_id": book_id,
        "distill_run_id": run_id,
        "status": result["status"] if result else "missing",
        "point_count": result["point_count"] if result else 0,
    }
