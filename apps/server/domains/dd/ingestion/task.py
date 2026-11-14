"""Celery bridge for ingestion. Queued from POST /api/v1/dd/ingestion/runs;
progress + manifest flow back through Redis."""
from __future__ import annotations
import domains, infra, infra.celery
from . import evaluation, params, runtime

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


@infra.celery.service.app.task(
    name=params.REPLAY_TASK_NAME,
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
    soft_time_limit=params.SOFT_TIME_LIMIT_S,
    time_limit=params.TIME_LIMIT_S,
)
def run_ingestion_replay(
    self,
    replay_id: str,
    evaluation_round_id: str,
    source_round_id: str,
    source_run_id: str,
    fixture_ids: list[str],
    lock_slug: str,
) -> dict:
    """Replay fixed source pages through audit/repair; never extracts, publishes or backfills vaults."""
    logger.info(
        "[task] run_ingestion_replay replay_id=%s source_run_id=%s fixtures=%s",
        replay_id,
        source_run_id,
        fixture_ids,
    )

    async def execute() -> dict:
        redis_client = infra.redis.service.new_redis(
            op_timeout_s=domains.dd.ingestion.runtime.dispatch.params.REDIS_OP_TIMEOUT_S,
        )
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("DD ingestion replay has no asyncio task context")
        watcher = asyncio.create_task(
            runtime.dispatch.service.cancel_watcher(
                redis_client,
                replay_id,
                current_task,
                slug=lock_slug,
            ),
        )
        try:
            return await evaluation.service.run_page_replay(
                replay_id,
                evaluation_round_id,
                source_round_id,
                source_run_id,
                fixture_ids,
                redis_client=redis_client,
            )
        finally:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass
            await runtime.dispatch.service.release_run(lock_slug, replay_id)
            try:
                await redis_client.aclose()
            except Exception:
                pass

    try:
        return asyncio.run(execute())
    except Exception as error:
        logger.exception("[task] DD ingestion fixed-input replay failed")
        return {
            "replay_id": replay_id,
            "status": "failed",
            "error": runtime.dispatch.domain.error_text(error),
        }
