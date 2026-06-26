"""ycs/pipeline_task — Celery chain dispatchers (Imperative Shell).

Per `docs/CODE-CONVENTIONS.md` §4: I/O orchestration goes in `service.py`.
This module builds the Celery `chain(...)` signatures, applies them to
the broker, walks the resulting `.parent` chain to capture every link's
task_id, and returns the IDs as a flat dict so the FastAPI layer can
hand them back to the FastHTML poller verbatim.

The chain semantics are guaranteed by Celery: every link's UUID is
assigned at chain-build time (not at run time), so `.parent` walking
gives us all IDs upfront — even for tasks that haven't been queued
yet. Polling against an as-yet-unqueued task returns `PENDING`, which
the UI renders as "queued".

`persist_pipeline_state` / `load_pipeline_state` snapshot the dispatch
inputs (`video_ids`, transcription flags) keyed by the extract task id
so the Ingest page's "Rerun" button can resurrect a run without making
the user re-pick videos from Search."""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis_aio
from celery import chain
from celery.result import AsyncResult

from .keys import pipeline_state_key
from .params import NEO4J_BATCH_SIZE, PIPELINE_STATE_TTL_S


logger = logging.getLogger(__name__)


def dispatch_videos_pipeline(
    video_ids:             list[str],
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Queue the 4-link Videos ingestion chain (extract → Qdrant →
    Neo4j → invalidate_cache) scoped to the supplied `video_ids`.

    Imports are deferred (function-local) because the Celery task
    modules import the worker app, and that app has a chain of imports
    that touch optional infra (langchain_neo4j, qdrant_client). Keeping
    these inside the function lets `domains/ycs/pipeline_task/` import
    cleanly in test environments without those deps installed.

    Returns `{extract, qdrant, neo4j, invalidate}` task_ids — the
    FastHTML poller treats `extract` / `qdrant` / `neo4j` as the three
    user-visible progress bars; `invalidate` is silent."""
    from domains.ycs.extract.task import extract_videos
    from domains.ycs.neo4j_task.task import ingest_to_neo4j
    from domains.ycs.qdrant_task.task import (
        ingest_to_qdrant,
        invalidate_cache,
    )

    chain_sig = chain(
        extract_videos.si(video_ids, include_transcription, languages),
        ingest_to_qdrant.si(video_ids),
        ingest_to_neo4j.si(video_ids, NEO4J_BATCH_SIZE),
        invalidate_cache.si(),
    )
    result: AsyncResult = chain_sig.apply_async()
    return _phase_ids_from_chain(result)


def _phase_ids_from_chain(result: AsyncResult) -> dict[str, str]:
    """Walk `.parent` from the chain's last AsyncResult to harvest every
    link's task_id. Returns a dict in chain order:
    `{extract, qdrant, neo4j, invalidate}`."""
    ids: list[str] = []
    cur: AsyncResult | None = result
    while cur is not None:
        ids.append(cur.id)
        cur = cur.parent
    ids.reverse()
    keys = ["extract", "qdrant", "neo4j", "invalidate"]
    return {keys[i]: ids[i] for i in range(min(len(keys), len(ids)))}


# Rerun state (Redis-backed)
async def persist_pipeline_state(
    redis:                 redis_aio.Redis | None,
    extract_id:            str,
    video_ids:             list[str],
    include_transcription: bool,
    languages:             list[str] | None,
    phases:                dict[str, str] | None = None,
) -> None:
    """Best-effort snapshot of the dispatch params to Redis. The
    FastHTML "Rerun" button POSTs the extract id back; the rerun
    endpoint reads this blob and re-fires the chain with the same
    args. TTL `PIPELINE_STATE_TTL_S` (24h). Failure logs a warning
    and falls through — the live run stays valid, only Rerun breaks.

    `phases` stores every chain link's task_id (extract / qdrant /
    neo4j / invalidate) so the Stop endpoint can revoke them all
    without making the client send their IDs back."""
    if redis is None or not extract_id:
        return
    payload: dict[str, Any] = {
        "video_ids":             list(video_ids),
        "include_transcription": bool(include_transcription),
        "languages":             list(languages) if languages else None,
    }
    if phases:
        payload["phases"] = dict(phases)
    try:
        await redis.set(
            pipeline_state_key(extract_id),
            json.dumps(payload, ensure_ascii = False),
            ex = PIPELINE_STATE_TTL_S,
        )
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline] persist failed for {extract_id}: "
            f"{type(e).__name__}: {e}"
        )


async def wipe_videos_data(
    video_ids:   list[str],
    neo4j_graph: Any | None = None,
) -> dict[str, Any]:
    """Best-effort 3-store wipe of every artifact tied to the supplied
    `video_ids` — ES metadata + transcripts, Qdrant hybrid points, and
    Neo4j Document + Video nodes. Used by the Pipeline panel's `Wipe
    cache` button so the next Retry re-runs the whole chain from
    scratch (no Phase 1 cache hits, no Phase 3 skip-on-video_id).

    Spawns fresh ES + Qdrant clients (FastAPI request context, not a
    long-lived pool) and closes them at exit. `neo4j_graph` is reused
    from `app.state.neo4j_graph` to avoid a fresh bolt handshake per
    wipe.

    Best-effort across all 3 stores — a failure in one store is logged
    and counted, the others still run. Returns a summary dict the
    Wipe button surfaces in the panel status text."""
    import os
    from elasticsearch import AsyncElasticsearch
    from qdrant_client import AsyncQdrantClient

    from domains.ycs.es_index import delete_videos_from_es
    from domains.ycs.graph_builder import delete_documents_for_videos
    from domains.ycs.ingestion import delete_points_for_videos

    if not video_ids:
        return {"status": "noop", "reason": "no video_ids"}

    summary: dict[str, Any] = {"video_ids": list(video_ids)}

    es = AsyncElasticsearch(
        hosts      = [os.environ["ELASTICSEARCH_HOST"]],
        basic_auth = (
            os.environ["ELASTICSEARCH_USERNAME"],
            os.environ.get("ELASTICSEARCH_PASSWORD", ""),
        ),
        verify_certs = False,
    )
    qdrant_api_key = os.environ.get("QDRANT_API_KEY")
    qdrant = AsyncQdrantClient(
        url     = os.environ.get("QDRANT_URL", "http://localhost:6333"),
        port    = int(os.environ.get("QDRANT_PORT", "6333")),
        api_key = qdrant_api_key if qdrant_api_key else None,
    )
    try:
        # 1. Elasticsearch — metadata + transcripts indexes
        summary["es"] = await delete_videos_from_es(es, video_ids)
        # 2. Qdrant — hybrid collection (dense + sparse)
        summary["qdrant"] = await delete_points_for_videos(qdrant, video_ids)
        # 3. Neo4j — Document + Video nodes (entities left intact;
        #    may be referenced by other videos' graphs)
        if neo4j_graph is not None:
            summary["neo4j"] = delete_documents_for_videos(
                neo4j_graph, video_ids,
            )
        else:
            summary["neo4j"] = {"skipped": "neo4j_graph not available"}
    finally:
        try:
            await qdrant.close()
        except Exception:
            pass
        try:
            await es.close()
        except Exception:
            pass

    logger.info(
        f"[ycs:pipeline:wipe] {len(video_ids)} video(s): {summary}"
    )
    return summary


def revoke_pipeline_phases(phase_ids: list[str]) -> dict[str, str]:
    """Send Celery revoke to every supplied task_id. `terminate=True`
    sends SIGTERM to the worker process running the task (or queues
    the revoke for tasks not yet started). Idempotent — re-revoking
    an already-terminal task is a no-op.

    Returns `{task_id: outcome}` for log/UI surfacing. `outcome` is
    `"revoked"` on success or `"error: …"` on failure (one bad ID
    doesn't sink the rest of the sweep)."""
    from celery.result import AsyncResult
    from infra.celery import app

    outcomes: dict[str, str] = {}
    for tid in phase_ids:
        if not tid:
            continue
        try:
            app.control.revoke(tid, terminate = True, signal = "SIGTERM")
            outcomes[tid] = "revoked"
        except Exception as e:
            outcomes[tid] = f"error: {type(e).__name__}: {e}"
            logger.warning(
                f"[ycs:pipeline] revoke failed for {tid}: "
                f"{type(e).__name__}: {e}"
            )
    return outcomes


async def load_pipeline_state(
    redis:      redis_aio.Redis | None,
    extract_id: str,
) -> dict[str, Any] | None:
    """Look up the `{video_ids, include_transcription, languages}` blob
    for a prior dispatch. Returns None on miss / parse error / Redis
    hiccup — the caller surfaces a 404 to the user."""
    if redis is None or not extract_id:
        return None
    try:
        raw = await redis.get(pipeline_state_key(extract_id))
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline] load failed for {extract_id}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
