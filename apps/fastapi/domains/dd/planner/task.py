"""Celery task wrapping the async LangGraph planner; progress streams over Redis pub/sub from the worker process."""
import domains
import infra.celery
from . import domain, service

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = "domains.dd.planner.task.run_planner",
    bind = True,
    acks_late = False,
    track_started = True,
    # 2026-09-09: soft_time_limit=3600/time_limit=3660 removed. The "4×
    # headroom over ~12-15 min" estimate this was based on assumed a
    # healthy-pool baseline — under real Rotator pool-contention (shared
    # "universal LLM server", provider-side rate-limit/capacity crunches)
    # a run can legitimately exceed 1h even on a mid-size corpus (confirmed
    # live: fastmcp, 394 docs, SoftTimeLimitExceeded at exactly 3600.08s
    # while doc_distill was still actively making progress — no hang, just
    # genuinely slow). Celery's signal-based timeout doesn't distinguish
    # "hung" from "slow", so it was killing healthy-but-slow runs outright.
    # Cancellation is handled manually instead, via the existing cooperative
    # cancel path (progress.raise_if_cancelled + the /cancel endpoint) —
    # see runtime/cancel/service.py. No blanket wall-clock cap.
)
def run_planner(self, thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Fresh planner pass; CAD-releases the single-flight lock in finally regardless of outcome."""
    logger.info(
        f"[task] run_planner thread_id={thread_id} slug={slug} mode={mode}"
    )
    try:
        try:
            return asyncio.run(
                service._init_and_run(domains.dd.planner.runtime.dispatch.service.run_planner_async(thread_id, slug, mode))
            )
        except Exception as e:
            logger.exception(f"[task] run_planner failed: {e}")
            return {
                "thread_id": thread_id,
                "slug": slug,
                "mode": mode,
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
    finally:
        service._release_planner_lock(slug, thread_id)


@infra.celery.service.app.task(
    name = "domains.dd.planner.task.resume_planner",
    bind = True,
    acks_late = False,
    track_started = True,
    # 2026-09-09: time limits removed — see run_planner's decorator above
    # for why. Managed manually via the cooperative cancel path instead.
)
def resume_planner(self, thread_id: str) -> dict:
    """Resume from last checkpoint; doesn't acquire the start lock but DOES CAD-release on completion (covers SIGKILL'd original tasks)."""
    logger.info(f"[task] resume_planner thread_id={thread_id}")
    slug = domain._slug_from_planner_thread_id(thread_id)
    try:
        try:
            return asyncio.run(
                service._init_and_run(domains.dd.planner.runtime.dispatch.service.resume_planner_async(thread_id))
            )
        except Exception as e:
            logger.exception(f"[task] resume_planner failed: {e}")
            return {
                "thread_id": thread_id,
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
    finally:
        if slug:
            service._release_planner_lock(slug, thread_id)
