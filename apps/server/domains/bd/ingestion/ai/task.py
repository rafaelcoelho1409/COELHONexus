"""Celery entry point for resumable Book Distiller agentic ingestion runs."""
from __future__ import annotations
import domains, infra.celery
from . import service

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = domains.bd.ingestion.ai.params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
)
def run_ingestion_ai(self, book_id: str, extraction_run_id: str, agentic_run_id: str) -> dict:
    """Run or resume one pinned agentic candidate with the Postgres checkpointer."""
    logger.info(
        f"[task] run_ingestion_ai book_id={book_id} extraction_run_id={extraction_run_id} "
        f"agentic_run_id={agentic_run_id}"
    )

    async def execute() -> dict:
        try:
            await infra.postgres.service.init_checkpointer()
            result = await domains.bd.ingestion.ai.service.run_ingestion_ai(
                book_id,
                expected_extraction_id = extraction_run_id,
                agentic_run_id = agentic_run_id,
                checkpointer = infra.postgres.service.get_checkpointer(),
                resume = True,
            )
            return {
                "book_id": book_id,
                "extraction_run_id": extraction_run_id,
                "agentic_run_id": agentic_run_id,
                "status": result.get("status", "complete"),
                "quality_report": result.get("quality_report"),
            }
        except Exception as error:
            logger.exception(f"[task] run_ingestion_ai failed for {agentic_run_id}")
            try:
                await service.mark_ingestion_ai_run_failed(book_id, agentic_run_id, error)
                failed = await service.read_ingestion_ai_run(book_id, agentic_run_id)
                if failed is not None:
                    await domains.bd.ingestion.ai.progress.service.emit(
                        book_id,
                        agentic_run_id,
                        "run_failed",
                        status = domains.bd.ingestion.ai.schemas.AgenticRunStatus.FAILED.value,
                        error = failed["error"],
                        progress = failed["progress"],
                    )
            except Exception:
                logger.exception(f"[task] unable to persist failure for {agentic_run_id}")
            raise
        finally:
            try:
                await domains.settings.llm_counter.service.snapshot("bd", agentic_run_id)
            except Exception:
                logger.exception(f"[task] unable to snapshot LLM counters for {agentic_run_id}")
            await infra.postgres.service.close_checkpointer()

    return asyncio.run(execute())
