"""ycs/pipeline_task — Celery chain wrapper for the full channel pipeline.
ONE task: `full_channel_pipeline(channel_id, max_results, ...)` chains
  extract_channel → {ingest_to_qdrant, ingest_to_neo4j concurrently} → invalidate_cache

`si()` = immutable signature (don't pass the previous task's result as
the first arg). When BOTH `include_qdrant` and `include_graph` are set,
Qdrant and Neo4j only depend on Phase 1's ES writes, not on each other,
so they're wrapped in a `group()` (implicit chord, `invalidate_cache`
as callback) to run concurrently instead of forming a needless barrier
— same rationale as `pipeline_task/service.py::dispatch_videos_pipeline`.
With only one of the two flags set, that single step runs plain
(a group of one buys nothing). `invalidate_cache` always runs last
(no-op if no ingestion happened upstream)."""
from __future__ import annotations

from typing import Any

from celery import chain, group
from celery.utils.log import get_task_logger

from domains.ycs.extract.task import extract_channel
from domains.ycs.neo4j_task.task import ingest_to_neo4j
from domains.ycs.qdrant_task.task import (
    ingest_to_qdrant,
    invalidate_cache,
)
import infra.celery.service


logger = get_task_logger(__name__)


@infra.celery.service.app.task(
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
    """Full pipeline: extract → {Qdrant, Neo4j concurrently} → clear cache.

    Each task runs in its own Celery worker (possibly on different queues).
    If any step fails, Celery retries that step — not the whole pipeline."""
    ingest_steps = []
    if include_qdrant:
        ingest_steps.append(ingest_to_qdrant.si())
    if include_graph:
        ingest_steps.append(ingest_to_neo4j.si())
    steps: list[Any] = [
        extract_channel.si(channel_id, max_results, include_transcription),
    ]
    if len(ingest_steps) > 1:
        steps.append(group(*ingest_steps))
    else:
        steps.extend(ingest_steps)
    steps.append(invalidate_cache.si())
    pipeline = chain(*steps)
    result = pipeline.apply_async()
    return {
        "pipeline_id": result.id,
        "steps":       [
            (s.name if hasattr(s, "name") else "group(qdrant, neo4j)")
            for s in steps
        ],
        "channel_id":  channel_id,
    }
