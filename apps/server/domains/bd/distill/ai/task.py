"""Celery entry point for resumable Book Distiller Distill runs."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

import domains
import infra.celery
import infra.redis

from . import params, service

logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = params.TASK_NAME,
    bind = True,
    acks_late = True,
    reject_on_worker_lost = True,
    track_started = True,
    max_retries = None,
)
def run_distill(self, book_id: str, run_id: str) -> dict:
    """Run one pinned Distill graph with the shared Postgres checkpointer."""
    logger.info("[bd-distill-task] book_id=%s run_id=%s", book_id, run_id)

    async def execute_run() -> dict:
        checkpointer_initialized = False
        try:
            manifest = await service.mark_run_started(book_id, run_id, self.request.id)
            if manifest["status"] in {"done", "cancelled"}:
                return {
                    "book_id": book_id,
                    "run_id": run_id,
                    "status": manifest["status"],
                    "report_key": manifest.get("report_key"),
                }
            await infra.postgres.service.init_checkpointer()
            checkpointer_initialized = True
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
            try:
                if checkpointer_initialized:
                    await infra.postgres.service.close_checkpointer()
            finally:
                await domains.settings.chat.service.close_shared_http_client()

    async def execute_with_lease() -> dict:
        owner = f"{self.request.id or run_id}:{uuid.uuid4().hex}"
        lease_key = domains.bd.distill.ai.keys.execution_lock_key(book_id, run_id)
        lease_lost = asyncio.Event()
        heartbeat: asyncio.Task | None = None
        async with infra.redis.service.client() as redis:
            acquired, current_owner = await infra.redis.service.try_lock(
                redis,
                lease_key,
                owner,
                params.RUN_EXECUTION_LOCK_LEASE_S,
            )
            if not acquired:
                logger.info(
                    "[bd-distill-task] duplicate delivery deferred book_id=%s run_id=%s owner=%s",
                    book_id,
                    run_id,
                    current_owner,
                )
                return {"execution_lock_busy": True, "owner": current_owner}

            async def renew_execution_lease() -> None:
                while True:
                    await asyncio.sleep(params.RUN_EXECUTION_LOCK_RENEW_S)
                    try:
                        renewed = await infra.redis.service.extend_if_owner(
                            redis,
                            lease_key,
                            owner,
                            params.RUN_EXECUTION_LOCK_LEASE_S,
                        )
                    except Exception:
                        logger.exception(
                            "[bd-distill-task] unable to renew execution lease for book=%s run=%s",
                            book_id,
                            run_id,
                        )
                        lease_lost.set()
                        return
                    if not renewed:
                        logger.error(
                            "[bd-distill-task] execution lease lost for book=%s run=%s",
                            book_id,
                            run_id,
                        )
                        lease_lost.set()
                        return

            heartbeat = asyncio.create_task(renew_execution_lease())
            run_task = asyncio.create_task(execute_run())
            lost_task = asyncio.create_task(lease_lost.wait())
            try:
                done, _ = await asyncio.wait(
                    (run_task, lost_task),
                    return_when = asyncio.FIRST_COMPLETED,
                )
                if lost_task in done and lease_lost.is_set() and not run_task.done():
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions = True)
                    current_owner = await redis.get(lease_key)
                    if isinstance(current_owner, bytes):
                        current_owner = current_owner.decode()
                    safe_to_finalize = current_owner == owner
                    if current_owner is None:
                        safe_to_finalize, _ = await infra.redis.service.try_lock(
                            redis,
                            lease_key,
                            owner,
                            params.RUN_EXECUTION_LOCK_LEASE_S,
                        )
                    if safe_to_finalize:
                        await service.mark_failed(
                            book_id,
                            run_id,
                            RuntimeError("Distill execution lease renewal failed; the checkpoint can be resumed."),
                        )
                        return {"book_id": book_id, "run_id": run_id, "status": "failed"}
                    return {"book_id": book_id, "run_id": run_id, "status": "lease_lost"}
                return await run_task
            finally:
                lost_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await lost_task
                if heartbeat is not None:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
                await infra.redis.service.release_if_owner(redis, lease_key, owner)

    result = asyncio.run(execute_with_lease())
    if result.get("execution_lock_busy"):
        raise self.retry(countdown = params.RUN_EXECUTION_LOCK_RETRY_S, max_retries = None)
    return result
