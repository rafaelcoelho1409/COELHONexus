"""Celery bridge for ingestion. Queued from POST /api/v1/dd/ingestion/runs;
progress + manifest flow back through Redis."""
from __future__ import annotations
import infra, infra.celery
from . import params, runtime

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name=params.TASK_NAME,
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
    soft_time_limit=params.SOFT_TIME_LIMIT_S,
    time_limit=params.TIME_LIMIT_S,
)
def run_ingestion(
    self,
    run_id: str,
    slug: str,
    source_kind: str | None = None,
    benchmark_slug: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Run docs ingestion for `slug`; manifest lands at `coelhonexus:dd:runs:{run_id}:*`."""
    logger.info(f"[task] run_ingestion run_id={run_id} slug={slug}")
    try:
        async def execute() -> dict:
            checkpointer_initialized = False
            dispatch_completed = False
            try:
                await infra.postgres.service.init_checkpointer()
                checkpointer_initialized = True
                result = await runtime.dispatch.service.run(
                    run_id,
                    slug,
                    source_kind=source_kind,
                    benchmark_slug=benchmark_slug,
                    round_id=round_id,
                )
                dispatch_completed = True
                return result
            except Exception as error:
                await runtime.dispatch.service.release_run(slug, run_id)
                if (
                    not dispatch_completed
                    and source_kind is not None
                    and benchmark_slug is not None
                    and round_id is not None
                ):
                    await runtime.dispatch.service.mark_benchmark_task_failed(
                        run_id,
                        slug,
                        source_kind,
                        benchmark_slug,
                        round_id,
                        error,
                    )
                raise
            finally:
                if checkpointer_initialized:
                    await infra.postgres.service.close_checkpointer()

        return asyncio.run(execute())
    except Exception as e:  # the task's result carries the failure; the worker must not crash on it
        logger.exception("[task] run_ingestion failed")
        return runtime.dispatch.domain.rejected_result(run_id, slug, runtime.dispatch.domain.error_text(e))
