"""Celery task: docs distiller planner.

Bridges Celery's sync execution model to the async LangGraph planner.
Queued from `POST /api/v1/docs-distiller/planner/{slug}` and `/resume`;
the worker picks tasks up from the `planner-{env}` queue, runs the full
8-node graph, and persists every checkpoint to Postgres via
AsyncPostgresSaver. The FastAPI SSE endpoint subscribes to the Redis
pub/sub channel the worker publishes to, so live progress streams to the
UI from the worker process.

Two tasks:
  - run_planner(thread_id, slug, mode):
        fresh kickoff; delegates to dispatch.run_planner_async.
  - resume_planner(thread_id):
        resume from last checkpoint; handles standard resume + catch-up
        for missing IMPLEMENTED nodes inside dispatch.resume_planner_async.

Both follow the canonical pattern from `domains/dd/ingestion/task.py`:
  asyncio.run() bridge + dict return + try/except → "failed" envelope.
"""
import asyncio
import logging

from celery_app import app

from .checkpoint import init_checkpointer
from .dispatch import resume_planner_async, run_planner_async


logger = logging.getLogger(__name__)


async def _init_and_run(coro):
    """Per-task: ensure the AsyncPostgresSaver is open in THIS worker
    process's interpreter (idempotent — module-scope cache guards), then
    run the requested coroutine. The init is async because
    AsyncPostgresSaver.from_conn_string returns an async context manager;
    each worker process opens its own connection pool once and reuses it
    across all tasks in that process."""
    await init_checkpointer()
    return await coro


@app.task(
    name="domains.dd.planner.task.run_planner",
    bind=True,
    acks_late=False,
    track_started=True,
    # Planner empirically takes 12-15min on LangChain-scale corpora (777 docs);
    # 60min soft / 65min hard gives 4× headroom for cold embed_corpus + bandit
    # warm-up + heavy off_topic. Same shape as ingestion (3600/3660).
    soft_time_limit=3600,
    time_limit=3660,
)
def run_planner(self, thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Run a fresh planner pass for `slug`. The graph emits progress
    events via Redis pub/sub (channel `dd:planner:{thread_id}:events`)
    that the FastAPI SSE endpoint streams to the UI."""
    logger.info(
        f"[task] run_planner thread_id={thread_id} slug={slug} mode={mode}"
    )
    try:
        return asyncio.run(
            _init_and_run(run_planner_async(thread_id, slug, mode))
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


@app.task(
    name="domains.dd.planner.task.resume_planner",
    bind=True,
    acks_late=False,
    track_started=True,
    soft_time_limit=3600,
    time_limit=3660,
)
def resume_planner(self, thread_id: str) -> dict:
    """Resume a planner run from its last LangGraph checkpoint. Handles
    standard ainvoke(None) resume + catch-up (missing newly-implemented
    nodes) internally."""
    logger.info(f"[task] resume_planner thread_id={thread_id}")
    try:
        return asyncio.run(
            _init_and_run(resume_planner_async(thread_id))
        )
    except Exception as e:
        logger.exception(f"[task] resume_planner failed: {e}")
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }
