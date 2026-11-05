"""Celery bridge for resumable Agentic AI candidates over saved DD pages."""

from __future__ import annotations
import infra
from .. import params
from . import service

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name=params.AGENTIC_TASK_NAME,
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
    soft_time_limit=params.SOFT_TIME_LIMIT_S,
    time_limit=params.TIME_LIMIT_S,
)
def run_agentic_candidate(self, run_id: str, slug: str) -> dict:
    """Run one isolated Agentic AI candidate without repeating documentation extraction."""
    logger.info(f"[task] run_agentic_candidate run_id={run_id} slug={slug}")
    try:

        async def execute() -> dict:
            await infra.postgres.service.init_checkpointer()
            try:
                return await service.run_candidate(run_id, slug)
            finally:
                await infra.postgres.service.close_checkpointer()

        return asyncio.run(execute())
    except Exception as error:
        logger.exception("[task] run_agentic_candidate failed")
        return {"run_id": run_id, "slug": slug, "status": "failed", "error": str(error)}
