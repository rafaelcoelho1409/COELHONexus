"""ycs/pipeline_task — Celery dispatcher (Imperative Shell).

Per `docs/CODE-CONVENTIONS.md` §4: I/O orchestration goes in `service.py`.

2026-09-13, superseding the earlier same-day `chain(extract,
group(qdrant, neo4j), invalidate)` design: Neo4j and Qdrant no longer
wait for Phase 1 to finish for ALL videos either. `extract_videos`
itself now dispatches each video's Neo4j + Qdrant work the instant
that video's transcript lands in ES (see `extract/task.py`'s
`_on_video_indexed`), so `dispatch_videos_pipeline` has nothing left to
build a static chain/chord OVER — there is no fixed, known-at-dispatch-
time set of "the qdrant task" / "the neo4j task" any more, only N
per-video tasks fired progressively as `extract_videos` runs.

That breaks the assumption this whole pipeline was built on: that
`qdrant`/`neo4j`/`invalidate` are each one real, poll-able Celery task
id known at dispatch time. The fix is `pipeline_task.streaming`'s
exactly-once atomic-counter finalize model (see that module's
docstring) instead of Celery's own canvas machinery — `invalidate_cache`
is now dispatched dynamically, from whichever per-video task turns out
to be last, not from a callback Celery wires up front.

Consequently this function now only dispatches `extract_videos` itself.
The returned `phases` dict still carries `qdrant`/`neo4j` keys (for
backward-compat with every consumer keyed on those names — the FastAPI
aggregator endpoints, the FastHTML poller, Rerun's truthy-check) but
their VALUE is `extract_id` itself, reused as the Redis lookup key for
`pipeline_task.streaming.get_phase_progress` — not a Celery task id.
`invalidate` is `""` since its real id isn't known until
`maybe_finalize` actually dispatches it.

`persist_pipeline_state` / `load_pipeline_state` snapshot the dispatch
inputs (`video_ids`, transcription flags) keyed by the extract task id
so the Ingest page's "Rerun" button can resurrect a run without making
the user re-pick videos from Search."""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis_aio
from celery.result import AsyncResult

from .keys import pipeline_cancel_key, pipeline_state_key
from .params import PIPELINE_CANCEL_TTL_S, PIPELINE_STATE_TTL_S


logger = logging.getLogger(__name__)


def dispatch_videos_pipeline(
    video_ids:             list[str],
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Queue `extract_videos`, which now self-dispatches every video's
    Neo4j + Qdrant streaming work as its transcripts land in ES (see
    this module's docstring for why there's no static chain/chord to
    build any more).

    Imports are deferred (function-local) because the Celery task
    modules import the worker app, and that app has a chain of imports
    that touch optional infra (langchain_neo4j, qdrant_client). Keeping
    these inside the function lets `domains/ycs/pipeline_task/` import
    cleanly in test environments without those deps installed.

    Returns `{extract, qdrant, neo4j, invalidate}` — `extract` is a
    real Celery task id; `qdrant`/`neo4j` are `extract_id` reused as the
    streaming-aggregator lookup key (not real task ids — see docstring);
    `invalidate` is `""` (its real id is only known once the run's
    exactly-once finalize actually dispatches it)."""
    from domains.ycs.extract.task import extract_videos

    result: AsyncResult = extract_videos.apply_async(
        args = (video_ids, include_transcription, languages),
    )
    extract_id = result.id
    return {
        "extract":    extract_id,
        "qdrant":     extract_id,
        "neo4j":      extract_id,
        "invalidate": "",
    }


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


def revoke_pipeline_phases(
    phase_ids: list[str], *, terminate: bool = False,
) -> dict[str, str]:
    """Send Celery revoke to every supplied task_id. Without `terminate`
    (the default, used by Stop — see `request_cancel` below for how the
    already-running task is asked to stop) this only discards tasks
    still sitting in the broker queue, never touched by a worker yet —
    safe, no signal sent to any live OS process. `terminate=True` (kept
    for Wipe, which needs a hard correctness guarantee — see that
    endpoint's docstring) sends SIGTERM to whatever worker process IS
    running the task.

    2026-09-14: Stop used to always pass `terminate=True`. Live-observed
    failure mode: SIGTERM against a task holding Playwright's Node
    driver subprocess left that worker's OS process idle but Celery's
    prefork pool bookkeeping still marked the slot busy — every task
    dispatched afterward sat in `reserved` forever (`celery inspect
    active` empty, `inspect reserved` showing `acknowledged: False,
    worker_pid: None`), requiring a full worker pod restart to clear.
    Cooperative cancellation (`request_cancel`) now handles the
    already-running case for Stop; this function only needs to sweep
    queued-but-unstarted tasks for that path.

    Idempotent — re-revoking an already-terminal task is a no-op.
    Returns `{task_id: outcome}` for log/UI surfacing. `outcome` is
    `"revoked"` on success or `"error: …"` on failure (one bad ID
    doesn't sink the rest of the sweep)."""
    from infra.celery import app

    outcomes: dict[str, str] = {}
    for tid in phase_ids:
        if not tid:
            continue
        try:
            kwargs: dict[str, Any] = {"terminate": terminate}
            if terminate:
                kwargs["signal"] = "SIGTERM"
            app.control.revoke(tid, **kwargs)
            outcomes[tid] = "revoked"
        except Exception as e:
            outcomes[tid] = f"error: {type(e).__name__}: {e}"
            logger.warning(
                f"[ycs:pipeline] revoke failed for {tid}: "
                f"{type(e).__name__}: {e}"
            )
    return outcomes


async def request_cancel(redis: redis_aio.Redis, extract_id: str) -> None:
    """Set the cooperative-cancel flag for `extract_id`. Checked by
    `extract_videos`' Playwright chunk loop and `ingest_to_neo4j`'s
    retry-pass loop at safe checkpoints — see `keys.pipeline_cancel_key`
    for why this replaced hard SIGTERM as Stop's primary mechanism."""
    try:
        await redis.set(
            pipeline_cancel_key(extract_id), "1", ex = PIPELINE_CANCEL_TTL_S,
        )
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline] request_cancel failed for {extract_id}: "
            f"{type(e).__name__}: {e}"
        )


async def is_pipeline_cancelled(
    redis: redis_aio.Redis, extract_id: str,
) -> bool:
    try:
        v = await redis.get(pipeline_cancel_key(extract_id))
    except Exception:
        return False
    return bool(v)


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
