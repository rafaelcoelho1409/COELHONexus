"""
Knowledge Distiller — Export Celery Task

Wraps the pandoc + genanki render services as a Celery task so the router
returns immediately (< 10ms) while the actual render (seconds for HTML/EPUB,
tens of seconds for xelatex PDFs) runs in the background.

Inputs are JSON-serializable (no Pydantic models crossing the Redis boundary):
  study_id, study_root, framework, format ∈ {"pdf", "html", "epub", "anki"}

Routing: `llm` queue — same queue as the main distiller task. Export is
CPU-bound (pandoc subprocess, sqlite+zip for genanki) but the workload is
fundamentally I/O-and-process, so it's fine to share a pool.

Progress reporting is minimal — exports are short enough that a single
PROGRESS → SUCCESS transition is sufficient for the SSE streamer.
"""
import asyncio
import os
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@app.task(
    bind = True,
    name = "tasks.knowledge.export.export_study",
    # Same rationale as run_knowledge_distiller — user-triggered (clicks
    # "Export to PDF"), non-idempotent (writes a new rendered artifact to
    # MinIO), re-triggerable by the user on failure. acks_late=False avoids
    # the same zombie-on-worker-restart class of failures that would have a
    # stale export task re-run a week later for a study the user already
    # forgot about.
    acks_late = False,
)
def export_study(
    self,
    study_id: str,
    study_root: str,
    framework: str,
    format: str) -> dict:
    """
    Render `study_root` as `format` and write the result to MinIO.

    Args:
        study_id: stable id of the source study (used for logging/tracing).
        study_root: MinIO key prefix where summary.md + chapterNN/* live.
        framework: display title that lands in the document metadata and
                   Anki top-level deck name.
        format: one of "pdf", "html", "epub", "anki".

    Returns a summary dict suitable for Celery's result backend:
        {
            "study_id": ...,
            "study_root": ...,
            "format": ...,
            "object_key": <minio key of the rendered artifact>,
            "bytes_written": int,
        }
    """
    if format not in ("pdf", "html", "epub", "anki"):
        raise ValueError(f"unsupported export format: {format!r}")

    logger.info(
        f"[KD-export:{study_id}] starting format={format} study_root={study_root}"
    )
    self.update_state(
        state = "PROGRESS",
        meta = {
            "study_id": study_id,
            "study_root": study_root,
            "format": format,
            "phase": "rendering",
        },
    )

    async def _run():
        from services.knowledge.storage import MinIOStudyStorage
        storage = MinIOStudyStorage(
            bucket = os.environ.get("MINIO_BUCKET_COELHONEXUS", "coelhonexus"),
            endpoint_url = os.environ.get("MINIO_ENDPOINT", "https://minio-api.YOUR_TAILNET_DOMAIN.ts.net"),
            access_key = os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        )
        # ensure_bucket is idempotent — safe if the worker runs before
        # the FastAPI process has finished its own lifespan provisioning.
        await storage.ensure_bucket()

        if format == "anki":
            from services.knowledge.anki import render_anki_deck
            key, n_bytes = await render_anki_deck(storage, study_root, framework)
        else:
            from services.knowledge.pandoc import render_study
            key, n_bytes = await render_study(storage, study_root, framework, format)

        return {
            "study_id": study_id,
            "study_root": study_root,
            "format": format,
            "object_key": key,
            "bytes_written": n_bytes,
        }

    try:
        result = asyncio.run(_run())
    except FileNotFoundError as e:
        logger.error(f"[KD-export:{study_id}] nothing to export: {e}")
        raise
    except Exception as e:
        logger.exception(f"[KD-export:{study_id}] failed: {e}")
        raise

    logger.info(
        f"[KD-export:{study_id}] done format={format} "
        f"key={result['object_key']} bytes={result['bytes_written']}"
    )
    return result
