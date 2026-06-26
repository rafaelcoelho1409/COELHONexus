"""ycs/pipeline_task — Celery chain wrapper for the full channel pipeline.
ONE task: `full_channel_pipeline(channel_id, max_results, ...)` chains
  extract_channel → ingest_to_qdrant → ingest_to_neo4j → invalidate_cache

`si()` = immutable signature (don't pass the previous task's result as
the first arg). Steps are appended conditionally based on the
`include_qdrant` / `include_graph` flags; `invalidate_cache` always runs
last (no-op if no ingestion happened upstream)."""
from __future__ import annotations

from typing import Any

from celery import chain
from celery.utils.log import get_task_logger

from domains.ycs.extract.task import extract_channel
from domains.ycs.neo4j_task.task import ingest_to_neo4j
from domains.ycs.qdrant_task.task import (
    ingest_to_qdrant,
    invalidate_cache,
)
from infra.celery import app


logger = get_task_logger(__name__)


@app.task(
    bind = True,
    name = "domains.ycs.pipeline_task.task.full_channel_pipeline",
)
def full_channel_pipeline(
    self,
    channel_id:            str,
    max_results:           int  = 0,
    include_transcription: bool = True,
    include_qdrant:        bool = True,
    include_graph:         bool = False,
) -> dict[str, Any]:
    """Full pipeline: extract → Qdrant vectors → Neo4j graph → clear cache.

    Each task runs in its own Celery worker (possibly on different queues).
    If any step fails, Celery retries that step — not the whole pipeline."""
    steps = [
        extract_channel.si(channel_id, max_results, include_transcription),
    ]
    if include_qdrant:
        steps.append(ingest_to_qdrant.si())
    if include_graph:
        steps.append(ingest_to_neo4j.si())
    steps.append(invalidate_cache.si())
    pipeline = chain(*steps)
    result = pipeline.apply_async()
    return {
        "pipeline_id": result.id,
        "steps":       [s.name for s in steps],
        "channel_id":  channel_id,
    }
