"""Celery entry point for resumable Book Distiller agentic ingestion runs."""
from __future__ import annotations
import infra.celery
from . import params, progress, runs, service
from .... import settings

import asyncio
import contextlib
import logging
import uuid


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
    max_retries = None,
)
def run_ingestion_ai(self, book_id: str, extraction_run_id: str, agentic_run_id: str) -> dict:
    """Run or resume one pinned agentic candidate with a single-writer lease."""
    logger.info(
        f"[task] run_ingestion_ai book_id={book_id} extraction_run_id={extraction_run_id} "
        f"agentic_run_id={agentic_run_id}"
    )

    async def execute() -> dict:
        owner = f"{self.request.id or agentic_run_id}:{uuid.uuid4().hex}"
        async with infra.redis.service.client() as redis:
            async with redis.lock(
                progress.keys.run_mutation_lock_key(book_id, agentic_run_id),
                timeout = 120,
                blocking_timeout = 30,
            ):
                acquired, current_owner = await progress.service.acquire_run_execution_lock(
                    redis,
                    book_id,
                    agentic_run_id,
                    owner,
                )
                if not acquired:
                    logger.info(
                        f"[task] run {agentic_run_id} is already executing; "
                        f"task will retry after the current worker releases its lease"
                    )
                    return {"execution_lock_busy": True, "owner": current_owner}

                try:
                    manifest = await service.read_ingestion_ai_run(book_id, agentic_run_id)
                except Exception:
                    await progress.service.release_run_execution_lock(
                        redis,
                        book_id,
                        agentic_run_id,
                        owner,
                    )
                    raise
                if manifest is None:
                    await progress.service.release_run_execution_lock(
                        redis,
                        book_id,
                        agentic_run_id,
                        owner,
                    )
                    raise FileNotFoundError(f"agentic run {agentic_run_id} was not found for book {book_id}")
                active_statuses = {
                    runs.schemas.AgenticRunStatus.QUEUED.value,
                    runs.schemas.AgenticRunStatus.RUNNING.value,
                }
                if manifest["status"] not in active_statuses:
                    await progress.service.release_run_execution_lock(
                        redis,
                        book_id,
                        agentic_run_id,
                        owner,
                    )
                    logger.info(
                        f"[task] ignoring stale delivery for terminal run {agentic_run_id} "
                        f"status={manifest['status']}"
                    )
                    return {
                        "book_id": book_id,
                        "extraction_run_id": extraction_run_id,
                        "agentic_run_id": agentic_run_id,
                        "status": manifest["status"],
                        "ignored_stale_delivery": True,
                    }

            lease_lost = asyncio.Event()

            async def renew_execution_lease() -> None:
                while True:
                    await asyncio.sleep(
                        progress.params.RUN_EXECUTION_LOCK_RENEW_S,
                    )
                    try:
                        renewed = await progress.service.renew_run_execution_lock(
                            redis,
                            book_id,
                            agentic_run_id,
                            owner,
                        )
                    except Exception:
                        logger.exception(
                            f"[task] unable to renew execution lease for {book_id}/{agentic_run_id}"
                        )
                        lease_lost.set()
                        return
                    if not renewed:
                        logger.error(
                            f"[task] execution lease ownership lost for {book_id}/{agentic_run_id}"
                        )
                        lease_lost.set()
                        return

            heartbeat = asyncio.create_task(renew_execution_lease())
            try:
                async def cancel_check() -> bool:
                    if lease_lost.is_set():
                        raise progress.service.RunExecutionLeaseLostError(
                            f"execution lease lost for {book_id}/{agentic_run_id}"
                        )
                    return await progress.service.cancel_requested(redis, book_id, agentic_run_id)

                async def execution_check() -> None:
                    if lease_lost.is_set():
                        raise progress.service.RunExecutionLeaseLostError(
                            f"execution lease lost for {book_id}/{agentic_run_id}"
                        )
                    try:
                        still_owner = await progress.service.owns_run_execution_lock(
                            redis,
                            book_id,
                            agentic_run_id,
                            owner,
                        )
                    except Exception as error:
                        lease_lost.set()
                        raise progress.service.RunExecutionLeaseLostError(
                            f"execution lease could not be verified for {book_id}/{agentic_run_id}"
                        ) from error
                    if not still_owner:
                        lease_lost.set()
                        raise progress.service.RunExecutionLeaseLostError(
                            f"execution lease lost for {book_id}/{agentic_run_id}"
                        )

                try:
                    await infra.postgres.service.init_checkpointer()
                    result = await service.run_ingestion_ai(
                        book_id,
                        expected_extraction_id = extraction_run_id,
                        agentic_run_id = agentic_run_id,
                        checkpointer = infra.postgres.service.get_checkpointer(),
                        resume = True,
                        cancel_check = cancel_check,
                        execution_check = execution_check,
                    )
                    return {
                        "book_id": book_id,
                        "extraction_run_id": extraction_run_id,
                        "agentic_run_id": agentic_run_id,
                        "status": result.get("status", "complete"),
                        "quality_report": result.get("quality_report"),
                    }
                except progress.service.RunExecutionLeaseLostError:
                    logger.warning(
                        f"[task] stopped graph execution after losing lease for {book_id}/{agentic_run_id}"
                    )
                    try:
                        regained_lease = await progress.service.renew_run_execution_lock(
                            redis,
                            book_id,
                            agentic_run_id,
                            owner,
                        )
                        if regained_lease:
                            lease_error = RuntimeError(
                                "The ingestion worker lost its exclusive execution lease. "
                                "Resume the run to continue from its saved checkpoint."
                            )
                            await settings.llm_counter.service.snapshot("bd", agentic_run_id)
                            await service.mark_ingestion_ai_run_failed(
                                book_id,
                                agentic_run_id,
                                lease_error,
                            )
                            failed = await service.read_ingestion_ai_run(book_id, agentic_run_id)
                            if failed is not None and failed["status"] == (
                                runs.schemas.AgenticRunStatus.FAILED.value
                            ):
                                await progress.service.emit(
                                    book_id,
                                    agentic_run_id,
                                    "run_failed",
                                    status = runs.schemas.AgenticRunStatus.FAILED.value,
                                    error = failed["error"],
                                    progress = failed["progress"],
                                )
                    except Exception:
                        logger.exception(
                            f"[task] unable to record lease loss for {book_id}/{agentic_run_id}"
                        )
                    return {
                        "book_id": book_id,
                        "extraction_run_id": extraction_run_id,
                        "agentic_run_id": agentic_run_id,
                        "status": "lease_lost",
                    }
                except Exception as error:
                    logger.exception(f"[task] run_ingestion_ai failed for {agentic_run_id}")
                    try:
                        failed = await service.read_ingestion_ai_run(book_id, agentic_run_id)
                        needs_terminal_event = False
                        if failed is not None and failed["status"] not in {
                            runs.schemas.AgenticRunStatus.DONE.value,
                            runs.schemas.AgenticRunStatus.FAILED.value,
                            runs.schemas.AgenticRunStatus.CANCELLED.value,
                        }:
                            await settings.llm_counter.service.snapshot("bd", agentic_run_id)
                            await service.mark_ingestion_ai_run_failed(book_id, agentic_run_id, error)
                            failed = await service.read_ingestion_ai_run(book_id, agentic_run_id)
                            needs_terminal_event = True
                        if (
                            needs_terminal_event
                            and failed is not None
                            and failed["status"] == runs.schemas.AgenticRunStatus.FAILED.value
                        ):
                            await progress.service.emit(
                                book_id,
                                agentic_run_id,
                                "run_failed",
                                status = runs.schemas.AgenticRunStatus.FAILED.value,
                                error = failed["error"],
                                progress = failed["progress"],
                            )
                    except Exception:
                        logger.exception(f"[task] unable to persist failure for {agentic_run_id}")
                    raise
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                try:
                    await infra.postgres.service.close_checkpointer()
                finally:
                    await progress.service.release_run_execution_lock(
                        redis,
                        book_id,
                        agentic_run_id,
                        owner,
                    )

    result = asyncio.run(execute())
    if result.get("execution_lock_busy"):
        raise self.retry(
            countdown = progress.params.RUN_EXECUTION_LOCK_RETRY_S,
            max_retries = None,
        )
    return result
