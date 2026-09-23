"""Celery tasks for the docs distiller synth pipeline."""
import domains
import infra.celery
from . import domain, params, service

import asyncio
import logging


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name = "domains.dd.synth.task.run_single_chapter",
    bind = True,
    acks_late = False,
    track_started = True,
    # Per-chapter synth runs typically 10-24 min; 60min soft gives 2-3×
    # headroom for slow chapters that hit max CoRefine iters. 65min hard
    # widened to 5min-past-soft 2026-09-11 (Celery's documented minimum gap
    # for a task to reliably clean up after the soft limit fires — the
    # graph.py wall-clock RETHINK gate is the primary defense now; this is
    # the last-resort backstop). Values from params.py (single source of
    # truth — graph.py's gate reads the soft limit too).
    soft_time_limit = params.SINGLE_CHAPTER_SOFT_TIME_LIMIT_S,
    time_limit = params.SINGLE_CHAPTER_HARD_TIME_LIMIT_S,
)
def run_single_chapter(
    self,
    thread_id: str,
    slug: str,
    chapter_id: str,
    mode: str = "quality",
) -> dict:
    """Run a single-chapter synth pass; releases coelhonexus:dd:synth:lock:{slug} on exit."""
    logger.info(
        f"[task] run_single_chapter thread_id={thread_id} slug={slug} "
        f"chapter_id={chapter_id} mode={mode}"
    )
    try:
        try:
            return asyncio.run(
                service._init_and_run(
                    domains.dd.synth.runtime.dispatch.service.run_single_chapter_async(
                        thread_id, slug, chapter_id, mode,
                    ),
                )
            )
        except Exception as e:
            logger.exception(f"[task] run_single_chapter failed: {e}")
            return {
                "thread_id": thread_id,
                "slug":      slug,
                "chapter_id": chapter_id,
                "mode":      mode,
                "status":    "failed",
                "error":     f"{type(e).__name__}: {e}",
            }
    finally:
        service._release_synth_lock(slug, thread_id)


@infra.celery.service.app.task(
    name = "domains.dd.synth.task.resume_synth",
    bind = True,
    acks_late = False,
    track_started = True,
    # Widened 2026-09-11 alongside run_single_chapter — see its comment.
    soft_time_limit = params.SINGLE_CHAPTER_SOFT_TIME_LIMIT_S,
    time_limit = params.SINGLE_CHAPTER_HARD_TIME_LIMIT_S,
)
def resume_synth(self, thread_id: str) -> dict:
    """Resume from last checkpoint; CAD-releases the lock so a racing fresh start is safe."""
    logger.info(f"[task] resume_synth thread_id={thread_id}")
    slug = domain._slug_from_synth_thread_id(thread_id)
    try:
        try:
            return asyncio.run(
                service._init_and_run(
                    domains.dd.synth.runtime.dispatch.service.resume_synth_async(thread_id),
                )
            )
        except Exception as e:
            logger.exception(f"[task] resume_synth failed: {e}")
            return {
                "thread_id": thread_id,
                "status":    "failed",
                "error":     f"{type(e).__name__}: {e}",
            }
    finally:
        if slug:
            service._release_synth_lock(slug, thread_id)


@infra.celery.service.app.task(
    name = "domains.dd.synth.task.run_study",
    bind = True,
    acks_late = False,
    track_started = True,
    # Study orchestrator runs N chapters back-to-back + book_harmonize.
    # FastMCP 8-chapter run = ~120 min; Claude Code 6-chapter ~100 min.
    # 6h soft / 6h05m hard gives 3× headroom even for 12-chapter books.
    soft_time_limit = 21600,
    time_limit = 21900,
)
def run_study(
    self,
    study_thread_id: str,
    slug: str,
    chapter_ids: list[str],
    mode: str = "quality",
) -> dict:
    """Run strict-order study orchestrator; releases coelhonexus:dd:synth:lock:{slug} on exit."""
    logger.info(
        f"[task] run_study study_thread_id={study_thread_id} slug={slug} "
        f"n_chapters={len(chapter_ids)} mode={mode}"
    )
    try:
        try:
            return asyncio.run(
                service._init_and_run(
                    domains.dd.synth.runtime.dispatch.service.run_study_async(
                        study_thread_id, slug, chapter_ids, mode,
                    ),
                )
            )
        except Exception as e:
            logger.exception(f"[task] run_study failed: {e}")
            return {
                "thread_id":    study_thread_id,
                "slug":         slug,
                "final_status": "failed",
                "error":        f"{type(e).__name__}: {e}",
            }
    finally:
        service._release_synth_lock(slug, study_thread_id)
