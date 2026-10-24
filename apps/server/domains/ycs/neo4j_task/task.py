"""ycs/neo4j_task — Celery: extract entities from FULL transcripts → Neo4j.

ONE task: `ingest_to_neo4j(video_ids?, batch_size=1, skip_resolution=False,
extract_id=None)`. Two callers, two shapes:
  - Bulk/legacy (`skip_resolution=False`): `video_ids` is the WHOLE
    batch, `batch_size` defaults to 1 (per-video progress-bar
    granularity; see `graph_builder.params.EXTRACT_CONCURRENCY` for the
    actual pool width used when `batch_size<=1`).
  - Streaming (`skip_resolution=True`, `extract_id` set,
    2026-09-13/14): `extract/service.py`'s `_on_video_indexed` calls this
    with a CHUNK of up to `EXTRACT_CONCURRENCY` video ids (accumulated
    as videos finish Phase 1, flushed once the chunk fills) and
    `batch_size=len(chunk)`, so `domains.ycs.graph_builder.service.extract_and_store_graph`'s internal
    pool width exactly matches the chunk — full concurrency within the
    chunk, starting well before the whole run's video batch clears
    Phase 1. (An earlier same-day version dispatched one Celery task
    PER VIDEO instead of per chunk — reverted after a live 25-video
    run showed it starving on the shared 2-slot Celery worker pool,
    serializing all 21 downstream videos to one-at-a-time and wasting
    the internal concurrency entirely.)

Internally:
  1. Fresh AsyncElasticsearch (worker process)
  2. Fresh `Neo4jGraph` — deprecated did NOT pass `refresh_schema=False`
     here (only in app.py). Preserve that omission per port-fidelity.
  3. Build the chat model via `domains.settings.chat.service.build_chat_model()`
     — talks to the Settings-page-configured external endpoint.
     2026-09-13: removed the local `pick_ycs_neo4j_deployment_bandit`/
     `record_ycs_neo4j_reward`/`release_ycs_provider_slot` calls — all
     three were confirmed no-ops. There is no local pool left to feed.
  4. Fetch transcripts + metadata from ES.
  5. `domains.ycs.graph_builder.service.build_video_metadata_graph` — Video/Channel nodes (no LLM cost).
  6. `domains.ycs.graph_builder.service.extract_and_store_graph` — LLM entity extraction, one real attempt
     per video. Videos that fail get retried (same connection, same
     model) up to `params.MAX_RETRY_PASSES` times — see that constant's
     comment for why this replaced the old "arm-swap" framing."""
from __future__ import annotations
import infra.celery
from . import service

import asyncio
from typing import Any

from celery.utils.log import get_task_logger


logger = get_task_logger(__name__)


@infra.celery.service.app.task(
    bind = True,
    name = "domains.ycs.neo4j_task.task.ingest_to_neo4j",
)
def ingest_to_neo4j(
    self,
    video_ids:       list[str] | None = None,
    batch_size:      int              = 1,
    skip_resolution: bool             = False,
    extract_id:      str | None       = None,
) -> dict[str, Any]:
    """Extract entities from FULL transcripts via the chat endpoint
    LLM → Neo4j. With `batch_size=1` (the per-video streaming caller's
    default) each call is one video, so per-video progress matches
    Phase 1 / Phase 2 granularity.

    Includes entity resolution post-processing via rapidfuzz — UNLESS
    `skip_resolution=True` (2026-09-13, per-video streaming callers).
    In that mode this task is one of N independent per-video dispatches
    for one pipeline run (`extract_id`); resolution can't safely run
    per-video (it would re-run the same whole-graph fuzzy-merge pass N
    times — the exact "4× redundant" bug a previous ship already fixed
    for the batched path). Instead, each streaming call reports its
    outcome to `pipeline_task.service.mark_video_done`; whichever call
    turns out to be the LAST one for the run's Neo4j phase (atomic
    counter reaching the total `extract_videos` recorded) runs
    resolution exactly once, then checks whether Qdrant's phase is also
    done to fire the run's `invalidate_cache`."""
    logger.info(
        f"[ingest_to_neo4j] Starting: video_ids={video_ids}, "
        f"batch_size={batch_size}, skip_resolution={skip_resolution}",
    )
    self.update_state(state = "PROGRESS", meta = {"phase": "init"})

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    result = asyncio.run(
        service.ingest_async(
            video_ids, batch_size, skip_resolution, extract_id,
            task_id     = self.request.id or "",
            progress_cb = _progress,
        )
    )
    logger.info(f"[ingest_to_neo4j] Done: {result}")
    return result
