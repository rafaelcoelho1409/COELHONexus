"""Celery tasks wrapping the async LangGraph planner; progress streams over Redis pub/sub from the worker process."""
import domains
import infra.celery
from . import service

import asyncio
import logging


logger = logging.getLogger(__name__)


# No soft or hard time limit on any task here (removed 2026-09-09). The "4x headroom over ~12-15 min" estimate assumed a healthy-pool
# baseline; under real Rotator pool contention a run can legitimately exceed 1h even on a mid-size corpus (confirmed live: fastmcp,
# 394 docs, SoftTimeLimitExceeded at exactly 3600.08s while doc_distill was still making progress). Celery's signal-based timeout cannot
# tell "hung" from "slow", so it killed healthy-but-slow runs. Cancellation is cooperative instead: the cancel flag and its watcher
# (runtime/cancel), raised by the /cancel endpoint.
@infra.celery.service.app.task(
    name = "domains.dd.pipeline.ai.task.run_planner",
    bind = True,
    acks_late = False,
    track_started = True,
)
def run_planner(self, thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Fresh planner pass; CAD-releases the single-flight lock in finally regardless of outcome."""
    logger.info(f"[task] run_planner thread_id={thread_id} slug={slug} mode={mode}")
    try:
        try:
            return asyncio.run(service.init_and_run(service.run_planner_async(thread_id, slug, mode)))
        except Exception as error:
            logger.exception(f"[task] run_planner failed: {error}")
            return {
                "thread_id": thread_id,
                "slug": slug,
                "mode": mode,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
    finally:
        service.release_planner_lock(slug, thread_id)


@infra.celery.service.app.task(
    name = "domains.dd.pipeline.ai.task.resume_planner",
    bind = True,
    acks_late = False,
    track_started = True,
)
def resume_planner(self, thread_id: str) -> dict:
    """Resume from last checkpoint; doesn't acquire the start lock but DOES CAD-release on completion (covers SIGKILL'd original tasks)."""
    logger.info(f"[task] resume_planner thread_id={thread_id}")
    slug = domains.dd.pipeline.runtime.dispatch.domain.slug_from_thread_id(thread_id)
    try:
        try:
            return asyncio.run(service.init_and_run(service.resume_planner_async(thread_id)))
        except Exception as error:
            logger.exception(f"[task] resume_planner failed: {error}")
            return {"thread_id": thread_id, "status": "failed", "error": f"{type(error).__name__}: {error}"}
    finally:
        if slug:
            service.release_planner_lock(slug, thread_id)


@infra.celery.service.app.task(
    name = "domains.dd.pipeline.ai.task.prune_old_checkpoints",
    bind = True,
    acks_late = False,
    track_started = True,
)
def prune_old_checkpoints(self, older_than_days: int = 30, dry_run: bool = False) -> dict:
    """Periodic maintenance sweep — trigger via an external scheduler (k8s CronJob, celery beat, or manual `.delay()`); nothing in
    this codebase invokes it automatically. See `runtime.checkpoint.service.prune_old_threads` for the eligibility rule and why
    MinIO and Redis are untouched."""
    logger.info(f"[task] prune_old_checkpoints older_than_days={older_than_days} dry_run={dry_run}")
    try:
        return asyncio.run(
            service.init_and_run(
                domains.dd.pipeline.runtime.checkpoint.service.prune_old_threads(older_than_days = older_than_days, dry_run = dry_run),
            ),
        )
    except Exception as error:
        logger.exception(f"[task] prune_old_checkpoints failed: {error}")
        return {
            "older_than_days": older_than_days,
            "dry_run": dry_run,
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
