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
import os

import redis as redis_sync

from celery_app import app

from ..planner.checkpoint import init_checkpointer
from .dispatch import (
    resume_synth_async,
    run_single_chapter_async,
    run_study_async,
)


logger = logging.getLogger(__name__)


# Same CAD-release pattern as the planner task — see planner/task.py for
# the full rationale (compare-and-delete avoids a slow finally clearing
# a lock held by a racing later start).
_CAD_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)


def _slug_from_synth_thread_id(thread_id: str) -> str | None:
    """Synth thread_ids come in two shapes:
      - per-chapter: `docs-distiller/synth/{slug}/{uuid}`  -> parts[2]
      - study:       `docs-distiller/study/{slug}/{uuid}`  -> parts[2]
    Both store under `dd:synth:lock:{slug}` so the same release call
    works for both. Returns None on malformed input."""
    parts = (thread_id or "").split("/", 3)
    if (
        len(parts) >= 3
        and parts[0] == "docs-distiller"
        and parts[1] in ("synth", "study")
    ):
        return parts[2] or None
    return None


def _release_synth_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release of `dd:synth:lock:{slug}` from the
    task's finally block. Same shape as `_release_planner_lock`."""
    if not slug or not thread_id:
        return
    try:
        host = os.environ.get(
            "REDIS_HOST", "redis-master.redis.svc.cluster.local",
        )
        port = os.environ.get("REDIS_PORT", "6379")
        pwd = os.environ.get("REDIS_PASSWORD", "")
        url = (
            f"redis://:{pwd}@{host}:{port}" if pwd
            else f"redis://{host}:{port}"
        )
        r = redis_sync.from_url(
            url, socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            r.eval(
                _CAD_RELEASE_LUA, 1,
                f"dd:synth:lock:{slug}", thread_id,
            )
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[task] synth lock release failed for slug={slug!r}: "
            f"{type(e).__name__}: {e}"
        )


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
    FastAPI SSE endpoint streams to the UI.

    The outer try/finally releases the global single-flight lock
    (`dd:synth:lock:{slug}`) acquired by POST /synth/{slug}, no matter
    how the task terminates."""
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
    nodes) internally.

    Like resume_planner: doesn't acquire the lock (resume picks up a
    dead task's thread), but DOES CAD-release on completion so the
    lock isn't held until its TTL after the resume finishes. CAD makes
    this safe even if a fresh start has acquired in the meantime."""
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
    chapter loop, runs `book_harmonize` if ≥2 chapters completed.

    Outer try/finally releases the global single-flight lock acquired
    by POST /synth/{slug} (study branch). Per-chapter runs spawned by
    the orchestrator do NOT acquire individual locks — they run within
    the umbrella of this study's lock."""
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
