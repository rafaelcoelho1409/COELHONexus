"""Celery tasks: docs distiller synth.

Bridges Celery's sync execution model to the async LangGraph synth.
Queued from the FastAPI synth endpoints; the worker picks tasks up
from the `synth-{env}` queue, runs the per-chapter graph (or study
orchestrator), and persists every checkpoint to Postgres via the
shared AsyncPostgresSaver. The FastAPI SSE endpoint subscribes to
the Redis pub/sub channels the worker publishes to, so live progress
streams to the UI from the worker process.

Three tasks (mirrors `domains/dd/planner/task.py` structurally):
  - run_single_chapter(thread_id, slug, chapter_id, mode):
        single-chapter run; delegates to dispatch.run_single_chapter_async.
  - resume_synth(thread_id):
        resume from last checkpoint; handles standard resume + catch-up
        for missing IMPLEMENTED nodes via dispatch.resume_synth_async.
  - run_study(study_thread_id, slug, chapter_ids, mode):
        strict-order orchestrator (Bundle 6 streaming) — runs all
        chapters then book_harmonize. Long-running (~80-120 min on
        6-chapter books today; ~30-45 min once `KD_STUDY_SEM > 1`).

Reuses the planner's checkpoint module: synth and planner threads share
the same AsyncPostgresSaver pool (same Postgres tables; threads are
namespaced by id prefix). `init_checkpointer()` is idempotent so any
task can call it safely on first execution in a worker process.
"""
import asyncio
import logging

from celery_app import app

from ..planner.checkpoint import init_checkpointer
from .dispatch import (
    resume_synth_async,
    run_single_chapter_async,
    run_study_async,
)


logger = logging.getLogger(__name__)


async def _init_and_run(coro):
    """Per-task: ensure the AsyncPostgresSaver is open in THIS worker
    process's interpreter (idempotent — module-scope cache guards),
    then run the requested coroutine. Same pattern as planner/task.py."""
    await init_checkpointer()
    return await coro


@app.task(
    name="domains.dd.synth.task.run_single_chapter",
    bind=True,
    acks_late=False,
    track_started=True,
    # Per-chapter synth runs typically 10-24 min; 60min soft / 65min hard
    # gives 2-3× headroom for slow chapters that hit max CoRefine iters.
    soft_time_limit=3600,
    time_limit=3660,
)
def run_single_chapter(
    self,
    thread_id: str,
    slug: str,
    chapter_id: str,
    mode: str = "quality",
) -> dict:
    """Run a single-chapter synth pass. The graph emits progress events
    via Redis pub/sub (channel `dd:synth:{thread_id}:events`) that the
    FastAPI SSE endpoint streams to the UI."""
    logger.info(
        f"[task] run_single_chapter thread_id={thread_id} slug={slug} "
        f"chapter_id={chapter_id} mode={mode}"
    )
    try:
        return asyncio.run(
            _init_and_run(
                run_single_chapter_async(thread_id, slug, chapter_id, mode),
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


@app.task(
    name="domains.dd.synth.task.resume_synth",
    bind=True,
    acks_late=False,
    track_started=True,
    soft_time_limit=3600,
    time_limit=3660,
)
def resume_synth(self, thread_id: str) -> dict:
    """Resume a synth run from its last LangGraph checkpoint. Handles
    standard ainvoke(None) resume + catch-up (missing newly-implemented
    nodes) internally."""
    logger.info(f"[task] resume_synth thread_id={thread_id}")
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


@app.task(
    name="domains.dd.synth.task.run_study",
    bind=True,
    acks_late=False,
    track_started=True,
    # Study orchestrator runs N chapters back-to-back + book_harmonize.
    # FastMCP 8-chapter run = ~120 min; Claude Code 6-chapter ~100 min.
    # 6h soft / 6h05m hard gives 3× headroom even for 12-chapter books.
    soft_time_limit=21600,
    time_limit=21900,
)
def run_study(
    self,
    study_thread_id: str,
    slug: str,
    chapter_ids: list[str],
    mode: str = "quality",
) -> dict:
    """Run the strict-order study orchestrator (Bundle 6) for `slug`.
    Each chapter runs to completion before the next starts. After the
    chapter loop, runs `book_harmonize` if ≥2 chapters completed."""
    logger.info(
        f"[task] run_study study_thread_id={study_thread_id} slug={slug} "
        f"n_chapters={len(chapter_ids)} mode={mode}"
    )
    try:
        return asyncio.run(
            _init_and_run(
                run_study_async(study_thread_id, slug, chapter_ids, mode),
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
