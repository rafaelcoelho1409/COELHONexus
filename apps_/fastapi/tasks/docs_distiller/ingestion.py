"""Celery task: docs distiller ingestion.

Bridges Celery's sync execution model to the async ingestion dispatcher.
Queued from `POST /api/v1/docs-distiller/runs`; the worker picks it up,
runs the full Resolver → Tier-X → Post-process pipeline, and the result
is fetched back via Redis (progress + manifest live there).
"""
import asyncio
import logging

from celery_app import app

from services.docs_distiller.ingestion.dispatch import run as _run_dispatch


logger = logging.getLogger(__name__)


@app.task(
    name="tasks.docs_distiller.ingestion.run_ingestion",
    bind=True,
    acks_late=False,             # idempotent re-trigger lives in the API layer
    track_started=True,
    # Docker-scale Tier 3 (1500+ pages) + Tier 4 BFS with Playwright fallback
    # can legitimately push past 30 min. Raised from 1800/1860 (2026-05-17).
    # The framework lock's TTL in progress.py (2100s) also got room.
    soft_time_limit=3600,        # 60 min soft cap
    time_limit=3660,             # 61 min hard kill
)
def run_ingestion(self, run_id: str, slug: str) -> dict:
    """Run docs ingestion for `slug` and persist the manifest under
    `dd:runs:{run_id}:*` in Redis. The HTTP layer reads from those keys."""
    logger.info(f"[task] run_ingestion run_id={run_id} slug={slug}")
    try:
        return asyncio.run(_run_dispatch(run_id, slug))
    except Exception as e:
        logger.exception(f"[task] run_ingestion failed: {e}")
        return {
            "run_id": run_id,
            "slug": slug,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }
