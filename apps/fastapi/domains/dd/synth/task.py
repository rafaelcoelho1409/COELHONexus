"""Celery tasks for the docs distiller synth pipeline."""
import asyncio
import logging

import redis as redis_sync

from infra.celery import app

from ..planner.runtime.checkpoint import init_checkpointer
from .runtime.dispatch import (
    resume_synth_async,
    run_single_chapter_async,
    run_study_async,
)
from .keys import lock_key, redis_url
from .params import REDIS_CONNECT_TIMEOUT_S, REDIS_OP_TIMEOUT_S


logger = logging.getLogger(__name__)


# CAD-release: avoids a slow finally clearing a lock held by a racing later start.
_CAD_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)


def _slug_from_synth_thread_id(thread_id: str) -> str | None:
    """Extract slug from thread_id (`docs-distiller/synth|study/{slug}/{uuid}`), or None."""
    parts = (thread_id or "").split("/", 3)
    if (
        len(parts) >= 3
        and parts[0] == "docs-distiller"
        and parts[1] in ("synth", "study")
    ):
        return parts[2] or None
    return None


def _release_synth_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release of `dd:synth:lock:{slug}` from the task finally block."""
    if not slug or not thread_id:
        return
    try:
        r = redis_sync.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout = REDIS_OP_TIMEOUT_S,
        )
        try:
            r.eval(_CAD_RELEASE_LUA, 1, lock_key(slug), thread_id)
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[task] synth lock release failed for slug={slug!r}: "
            f"{type(e).__name__}: {e}"
        )


async def _init_and_run(coro):
    """Init checkpointer (idempotent) then run coro."""
    await init_checkpointer()
    return await coro


@app.task(
    name = "domains.dd.synth.task.run_single_chapter",
    bind = True,
    acks_late = False,
    track_started = True,
    # Per-chapter synth runs typically 10-24 min; 60min soft / 65min hard
    # gives 2-3× headroom for slow chapters that hit max CoRefine iters.
    soft_time_limit = 3600,
    time_limit = 3660,
)
def run_single_chapter(
    self,
    thread_id: str,
    slug: str,
    chapter_id: str,
    mode: str = "quality",
) -> dict:
    """Run a single-chapter synth pass; releases dd:synth:lock:{slug} on exit."""
    logger.info(
        f"[task] run_single_chapter thread_id={thread_id} slug={slug} "
        f"chapter_id={chapter_id} mode={mode}"
    )
    try:
        try:
            return asyncio.run(
                _init_and_run(
                    run_single_chapter_async(
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
        _release_synth_lock(slug, thread_id)


@app.task(
    name = "domains.dd.synth.task.resume_synth",
    bind = True,
    acks_late = False,
    track_started = True,
    soft_time_limit = 3600,
    time_limit = 3660,
)
def resume_synth(self, thread_id: str) -> dict:
    """Resume from last checkpoint; CAD-releases the lock so a racing fresh start is safe."""
    logger.info(f"[task] resume_synth thread_id={thread_id}")
    slug = _slug_from_synth_thread_id(thread_id)
    try:
        try:
            return asyncio.run(
                _init_and_run(resume_synth_async(thread_id))
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
            _release_synth_lock(slug, thread_id)


@app.task(
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
    """Run strict-order study orchestrator; releases dd:synth:lock:{slug} on exit."""
    logger.info(
        f"[task] run_study study_thread_id={study_thread_id} slug={slug} "
        f"n_chapters={len(chapter_ids)} mode={mode}"
    )
    try:
        try:
            return asyncio.run(
                _init_and_run(
                    run_study_async(
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
        _release_synth_lock(slug, study_thread_id)
