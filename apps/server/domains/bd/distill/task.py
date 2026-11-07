"""Celery entry point for resumable Book Distiller Distill runs."""
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
def run_distill(self, book_id: str, run_id: str) -> dict:
    """Run one pinned Distill graph with the shared Postgres checkpointer."""
    logger.info("[bd-distill-task] book_id=%s run_id=%s", book_id, run_id)

    async def execute() -> dict:
        try:
            await infra.postgres.service.init_checkpointer()
            result = await service.run_distill(
                book_id,
                run_id,
                checkpointer = infra.postgres.service.get_checkpointer(),
            )
            return {
                "book_id": book_id,
                "run_id": run_id,
                "status": result.get("status", "done"),
                "report_key": result.get("report_key"),
            }
        except Exception as error:
            logger.exception("[bd-distill-task] run failed for %s", run_id)
            try:
                await service.mark_failed(book_id, run_id, error)
            except Exception:
                logger.exception("[bd-distill-task] unable to persist failure for %s", run_id)
            raise
        finally:
            await infra.postgres.service.close_checkpointer()

    return asyncio.run(execute())
