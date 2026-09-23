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
id known at dispatch time. The fix is `pipeline_task.service`'s
exactly-once atomic-counter finalize model (see that module's
docstring) instead of Celery's own canvas machinery — `invalidate_cache`
is now dispatched dynamically, from whichever per-video task turns out
to be last, not from a callback Celery wires up front.

Consequently this function now only dispatches `extract_videos` itself.
The returned `phases` dict still carries `qdrant`/`neo4j` keys (for
backward-compat with every consumer keyed on those names — the FastAPI
aggregator endpoints, the FastHTML poller, Rerun's truthy-check) but
their VALUE is `extract_id` itself, reused as the Redis lookup key for
`pipeline_task.service.get_phase_progress` — not a Celery task id.
`invalidate` is `""` since its real id isn't known until
`maybe_finalize` actually dispatches it.

`persist_pipeline_state` / `load_pipeline_state` snapshot the dispatch
inputs (`video_ids`, transcription flags) keyed by the extract task id
so the Ingest page's "Rerun" button can resurrect a run without making
the user re-pick videos from Search."""
from __future__ import annotations
import domains
import domains.ycs.qdrant_task.task
import domains.ycs.extract.task
import infra.celery

import json
import logging
import os

from typing import Any

import redis.asyncio as redis_aio
from celery.result import AsyncResult
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from . import keys, params


logger = logging.getLogger(__name__)

PHASES = ("neo4j", "qdrant")


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
    result: AsyncResult = domains.ycs.extract.task.extract_videos.apply_async(
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
    args. TTL `params.PIPELINE_STATE_TTL_S` (24h). Failure logs a warning
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
            keys.pipeline_state_key(extract_id),
            json.dumps(payload, ensure_ascii = False),
            ex = params.PIPELINE_STATE_TTL_S,
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
        summary["es"] = await domains.ycs.es_index.service.delete_videos_from_es(es, video_ids)
        # 2. Qdrant — hybrid collection (dense + sparse). 2026-09-15:
        # Qdrant point payloads carry `video_id` only (a split video's
        # chunks are tagged with the PARTITION id, e.g. "xyz#p1" — there
        # is no `parent_video_id` payload field to OR against, unlike ES
        # transcripts/Neo4j Documents, which both have one). Expand the
        # requested ids with any known partition ids first — ES's
        # transcripts index still has the parent→partition mapping even
        # for docs ingested before this fix, so this stays correct for
        # old data too, without a Qdrant payload schema change.
        expanded_ids = await domains.ycs.ingestion.service.expand_with_partition_ids(es, video_ids)
        summary["qdrant"] = await domains.ycs.ingestion.service.delete_points_for_videos(qdrant, expanded_ids)
        # 3. Neo4j — Document + Video nodes (entities left intact;
        #    may be referenced by other videos' graphs)
        if neo4j_graph is not None:
            summary["neo4j"] = domains.ycs.graph_builder.service.delete_documents_for_videos(
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
    outcomes: dict[str, str] = {}
    for tid in phase_ids:
        if not tid:
            continue
        try:
            kwargs: dict[str, Any] = {"terminate": terminate}
            if terminate:
                kwargs["signal"] = "SIGTERM"
            infra.celery.service.app.control.revoke(tid, **kwargs)
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
            keys.pipeline_cancel_key(extract_id), "1", ex = params.PIPELINE_CANCEL_TTL_S,
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
        v = await redis.get(keys.pipeline_cancel_key(extract_id))
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
        raw = await redis.get(keys.pipeline_state_key(extract_id))
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


# Per-video streaming coordination (2026-09-13: the per-video streaming
# fan-out — Playwright/ES → Neo4j + Qdrant starting per video, not after
# the whole batch — replaces the single "one Celery task per phase" model
# with N independent per-video tasks for Neo4j and N for Qdrant. That
# breaks the assumption every other piece of this pipeline was built on —
# that `neo4j`/`qdrant` are each ONE task whose completion is directly
# observable. What follows is the replacement completion model: atomic
# Redis counters + an exactly-once finalize trigger, instead of Celery's
# own chain/chord machinery (which requires the full task set to be known
# at canvas-build time — true here for the total COUNT, but not for WHEN
# each one should start, which is the entire point of streaming them as
# videos complete).
#
# Exactly-once finalize, without a lock on the hot path:
#   1. `extract_videos` sets `phase_total_key(id, phase)` to the exact
#      count of videos it dispatched to that phase, once — after its own
#      dispatch loop finishes (this is the ONLY point that count is
#      ever known).
#   2. Every per-video task, on completion, calls `mark_video_done`,
#      which does an atomic `INCR` on `phase_finished_key`. Because INCR
#      is atomic and strictly monotonic, at most ONE caller's INCR can
#      ever return any given integer — so "did MY increment reach the
#      total" is a race-free way to detect "I am the last one," with no
#      lock needed for this check itself.
#   3. Whichever caller (a per-video task, or `extract_videos` itself,
#      covering the rare case where every video finishes before the
#      total is even set) observes BOTH phases' finished == total takes
#      the `finalize_flag_key` via `SET ... NX` — exactly one caller can
#      ever win that, regardless of how many observe the condition true
#      at once — and is the one that fires `invalidate_cache`.
#
# All Redis keys share `params.PIPELINE_STATE_TTL_S` so a crashed run's
# bookkeeping doesn't leak forever.


def build_redis_client() -> redis_aio.Redis:
    """Fresh client for a Celery task's own event loop — same pattern
    as `qdrant_task/task.py::invalidate_cache` (a Celery worker process
    is not the FastAPI process; there's no shared `app.state.redis_aio`
    to reuse here)."""
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = os.environ.get("REDIS_PORT", "6379")
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    url = (
        f"redis://:{redis_password}@{redis_host}:{redis_port}"
        if redis_password
        else f"redis://{redis_host}:{redis_port}"
    )
    return redis_aio.from_url(url)


async def set_phase_total(
    redis: redis_aio.Redis, extract_id: str, phase: str, total: int,
) -> tuple[int, int]:
    """Called by `extract_videos`: once UPFRONT (seeded to the full
    requested count, for early progress feedback while Playwright is
    still running) and once FINAL (corrected down to the count of
    videos that actually got a real per-video/chunk task dispatched —
    no-transcript/fetch-failed/index-write-failed videos are excluded
    entirely, not counted as failures in a store they were never
    eligible to reach).

    Returns `(finished, total)` — the finished counter read
    immediately after writing `total` — so a caller can tell whether
    THIS write is itself the moment the phase becomes complete (every
    dispatched video already reported in before the total was
    corrected to its final, lower value). Same race
    `mark_video_done`'s per-video completion already has to handle,
    generalized to the total-correction path: with the old design
    (total seeded once as the full requested count and never
    corrected), the only way a total-set could "complete" a phase was
    via a synthetic skip-mark; now that totals are genuinely corrected
    downward, the correction itself needs the same completion check."""
    await redis.set(keys.phase_total_key(extract_id, phase), total, ex = params.PIPELINE_STATE_TTL_S)
    raw_finished = await redis.get(keys.phase_finished_key(extract_id, phase))
    finished = int(raw_finished) if raw_finished is not None else 0
    return finished, total


async def set_phase_piece_total(
    redis: redis_aio.Redis, extract_id: str, phase: str, total: int,
) -> None:
    """Companion to `set_phase_total` — the individual-PIECE count
    (partitions + unsplit videos), not the video-level count. See
    `keys.phase_piece_total_key`. Purely informational (the Neo4j
    bar's "K/M pieces" display) — never read by `maybe_finalize` or
    any other completion logic, so it carries no correctness weight;
    a failed write just means the bar falls back to video-level
    K/N until/unless the caller retries."""
    try:
        await redis.set(
            keys.phase_piece_total_key(extract_id, phase), total, ex = params.PIPELINE_STATE_TTL_S,
        )
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] piece-total set failed for "
            f"{phase}: {type(e).__name__}: {e}"
        )


async def mark_video_done(
    redis:      redis_aio.Redis,
    extract_id: str,
    phase:      str,
    video_id:   str,
    success:    bool,
    extra:      dict[str, Any] | None = None,
) -> tuple[int, int | None]:
    """Record one video's terminal outcome for `phase` and bump the
    finished counter. Returns `(finished, total)` — `total` is `None`
    if `extract_videos` hasn't set it yet (dispatch still in flight).

    Also writes a per-video status entry (`phase_status_key`) so the
    FastAPI aggregator endpoint can render per-video rows the same way
    `/admin/task/{id}`'s `completed_ids`/`failed_ids` did for the old
    single-task-per-phase model."""
    payload = {"success": bool(success), **(extra or {})}
    try:
        await redis.hset(
            keys.phase_status_key(extract_id, phase), video_id, json.dumps(payload),
        )
        await redis.expire(keys.phase_status_key(extract_id, phase), params.PIPELINE_STATE_TTL_S)
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] status write failed for "
            f"{phase}/{video_id}: {type(e).__name__}: {e}"
        )
    finished = await redis.incr(keys.phase_finished_key(extract_id, phase))
    await redis.expire(keys.phase_finished_key(extract_id, phase), params.PIPELINE_STATE_TTL_S)
    raw_total = await redis.get(keys.phase_total_key(extract_id, phase))
    total = int(raw_total) if raw_total is not None else None
    return int(finished), total


async def mark_video_or_partition_done(
    redis:            redis_aio.Redis,
    extract_id:       str,
    phase:            str,
    video_id:         str,
    success:          bool,
    extra:            dict[str, Any] | None = None,
    *,
    parent_video_id:  str | None = None,
    part_total:       int | None = None,
) -> tuple[int, int | None] | tuple[None, None]:
    """2026-09-14: long-video partitioning. `video_id` here may be one
    partition of a video split into `part_total` pieces
    (`parent_video_id` set, e.g. `video_id="XYZ#p3"`,
    `parent_video_id="XYZ"`). A partition finishing must NOT bump the
    global `phase_finished_key` counter directly — that would count a
    5-partition video as 5 toward the phase total instead of 1, and
    the drawer row for "XYZ" (the id the user actually requested and
    the only one ever shown) would never see a `mark_video_done` call
    for its own literal id, since nothing dispatches "XYZ" itself once
    it's split.

    Every partition's outcome is instead recorded into a per-parent
    tracker (`partition_group_key`); only once every expected partition
    has reported does this fire exactly ONE `mark_video_done` call for
    `parent_video_id`, with the aggregated success (AND of all parts)
    and summed numeric `extra` fields (nodes_created, points_upserted,
    etc. — same shape a single video's own `extra` would carry).

    Callers with `parent_video_id=None` (every non-split video — the
    overwhelming majority) get the EXACT same direct `mark_video_done`
    call as before this function existed — zero behavior change.

    Returns `(finished, total)` from the eventual `mark_video_done`
    call once the group completes, or `(None, None)` while siblings
    are still pending — callers MUST treat `(None, None)` as "not this
    video's turn to be reported yet," not as an error, and must not
    call `maybe_finalize`/drain logic keyed off it in that case.

    2026-09-15: also bumps `phase_piece_finished_key` — a PIECE-level
    counter (every call counts, whether or not it's the one completing
    a partition group), paired with `phase_piece_total_key`. Purely
    additive display data for the Neo4j bar's "K/M pieces" — never
    gates finalize/completion logic, unlike the video-level counter
    below."""
    try:
        await redis.incr(keys.phase_piece_finished_key(extract_id, phase))
        await redis.expire(
            keys.phase_piece_finished_key(extract_id, phase), params.PIPELINE_STATE_TTL_S,
        )
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] piece-finished incr failed for "
            f"{phase}/{video_id}: {type(e).__name__}: {e}"
        )
    if not parent_video_id or parent_video_id == video_id:
        return await mark_video_done(redis, extract_id, phase, video_id, success, extra)

    group_key = keys.partition_group_key(extract_id, phase, parent_video_id)
    try:
        await redis.hset(
            group_key, video_id,
            json.dumps({"success": bool(success), **(extra or {})}),
        )
        await redis.expire(group_key, params.PIPELINE_STATE_TTL_S)
        reported = await redis.hgetall(group_key)
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] partition-group write failed for "
            f"{phase}/{parent_video_id}/{video_id}: {type(e).__name__}: {e}"
        )
        return None, None

    if part_total and len(reported) < part_total:
        # Siblings still pending — this partition's own outcome is
        # safely recorded above; the group just isn't complete yet.
        return None, None

    all_success = True
    agg_extra: dict[str, Any] = {}
    for raw in reported.values():
        try:
            entry = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            continue
        if not entry.get("success"):
            all_success = False
        for k, v in entry.items():
            if k == "success" or not isinstance(v, (int, float)):
                continue
            agg_extra[k] = agg_extra.get(k, 0) + v
    logger.info(
        f"[ycs:pipeline:streaming] {extract_id}: all {len(reported)} "
        f"partition(s) of {parent_video_id} reported for {phase} — "
        f"aggregate success={all_success}"
    )
    return await mark_video_done(
        redis, extract_id, phase, parent_video_id, all_success, agg_extra,
    )


async def update_video_extra(
    redis: redis_aio.Redis, extract_id: str, phase: str, video_id: str, extra: dict[str, Any],
) -> None:
    """Merge additional fields into a video's already-recorded status
    entry, WITHOUT touching the finished counter (`mark_video_done`
    already incremented it once for this video — calling that function
    again would double-count). Used when a value (like the whole-run
    entity-resolution merge count) is only known AFTER the video that
    turned out to be 'last' already reported in."""
    try:
        raw = await redis.hget(keys.phase_status_key(extract_id, phase), video_id)
        entry = json.loads(raw) if raw else {}
        entry.update(extra)
        await redis.hset(keys.phase_status_key(extract_id, phase), video_id, json.dumps(entry))
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] status merge failed for "
            f"{phase}/{video_id}: {type(e).__name__}: {e}"
        )


async def update_phase_preview(
    redis: redis_aio.Redis, extract_id: str, phase: str, completed_ids: list[str],
) -> None:
    """Best-effort ADD to the display-only in-progress preview (see
    `keys.phase_preview_key`). Called by chunked phase tasks as videos
    complete INSIDE the chunk; never touches the finalize counters.
    Failures log and swallow — progress display must never break
    extraction.

    2026-09-14: SADD (atomic union), NOT a JSON-list `SET` (overwrite)
    — with EXTRACT_CONCURRENCY > 1, multiple chunk tasks run
    concurrently, each tracking only ITS OWN local `completed_ids`.
    A blind overwrite meant whichever chunk's callback fired last
    replaced the shared key with only its own small set, erasing
    another still-in-flight chunk's already-displayed progress —
    observed live as the bar jumping back down (e.g. 40% -> 0% -> 40%)
    instead of advancing monotonically. SADD lets every chunk
    contribute its own ids without clobbering siblings'."""
    ids = [vid for vid in completed_ids if vid]
    if not extract_id or not ids:
        return
    try:
        key = keys.phase_preview_key(extract_id, phase)
        await redis.sadd(key, *ids)
        await redis.expire(key, params.PIPELINE_STATE_TTL_S)
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] preview write failed for "
            f"{phase}: {type(e).__name__}: {e}"
        )


async def track_dispatched_task(
    redis: redis_aio.Redis, extract_id: str, task_id: str,
) -> None:
    """Best-effort record of a fired per-video task id so Stop can
    revoke everything actually in flight, not just a fixed 4 ids."""
    try:
        await redis.rpush(keys.dispatched_tasks_key(extract_id), task_id)
        await redis.expire(keys.dispatched_tasks_key(extract_id), params.PIPELINE_STATE_TTL_S)
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] dispatched-task tracking failed "
            f"for {task_id}: {type(e).__name__}: {e}"
        )


async def get_dispatched_task_ids(
    redis: redis_aio.Redis, extract_id: str,
) -> list[str]:
    try:
        raw = await redis.lrange(keys.dispatched_tasks_key(extract_id), 0, -1)
    except Exception:
        return []
    return [
        (t.decode() if isinstance(t, (bytes, bytearray)) else t)
        for t in raw
    ]


async def maybe_finalize(redis: redis_aio.Redis, extract_id: str) -> bool:
    """Check whether BOTH streaming phases have reached their totals;
    if so, take the exactly-once finalize flag and fire
    `invalidate_cache`. Returns True only for the single caller that
    actually won the race and dispatched it — every other caller
    (including ones that correctly observed the condition) gets False.

    Safe to call speculatively from anywhere (every per-video task
    completion, plus `extract_videos` right after it sets the totals)
    — it's a cheap set of Redis reads until the condition is actually
    met, and the `SET NX` guard makes a double-fire structurally
    impossible regardless of how many callers race into this at once."""
    for phase in PHASES:
        raw_total = await redis.get(keys.phase_total_key(extract_id, phase))
        if raw_total is None:
            return False  # extract_videos hasn't finished dispatching yet
        raw_finished = await redis.get(keys.phase_finished_key(extract_id, phase))
        finished = int(raw_finished) if raw_finished is not None else 0
        if finished < int(raw_total):
            return False
    got_lock = await redis.set(
        keys.finalize_flag_key(extract_id), "1", nx = True, ex = params.PIPELINE_STATE_TTL_S,
    )
    if not got_lock:
        return False  # another caller already finalized this run
    logger.info(
        f"[ycs:pipeline:streaming] {extract_id}: both phases complete, "
        f"finalizing (invalidate_cache)"
    )
    domains.ycs.qdrant_task.task.invalidate_cache.delay()
    return True


async def get_phase_progress(
    redis: redis_aio.Redis, extract_id: str, phase: str,
) -> dict[str, Any]:
    """Synthesize the `{state, meta}` shape `/admin/task/{id}` normally
    returns, from this module's Redis counters/status hash, so the
    FastAPI aggregator endpoint can hand the FastHTML poller something
    shaped exactly like what it already knows how to render."""
    raw_total = await redis.get(keys.phase_total_key(extract_id, phase))
    total = int(raw_total) if raw_total is not None else None
    raw_finished = await redis.get(keys.phase_finished_key(extract_id, phase))
    finished = int(raw_finished) if raw_finished is not None else 0
    # 2026-09-15: PIECE-level counters (partitions counted individually,
    # unlike `total`/`finished` above which count a split video as 1) —
    # purely for display, see `keys.phase_piece_total_key`.
    raw_piece_total = await redis.get(keys.phase_piece_total_key(extract_id, phase))
    piece_total = int(raw_piece_total) if raw_piece_total is not None else None
    raw_piece_finished = await redis.get(keys.phase_piece_finished_key(extract_id, phase))
    piece_finished = int(raw_piece_finished) if raw_piece_finished is not None else 0
    try:
        raw_status = await redis.hgetall(keys.phase_status_key(extract_id, phase))
    except Exception:
        raw_status = {}
    completed_ids: list[str] = []
    failed_ids: list[str] = []
    # Summed across every video's `extra` payload (set by
    # `neo4j_task.task`/`qdrant_task.task` on success) so the FastHTML
    # success hint (`_successHint` in `pipeline_panel.js`) has real
    # numbers instead of `undefined` — those functions expect the same
    # aggregate shape the old single-task result dicts carried.
    numeric_totals: dict[str, int] = {}
    for k, v in (raw_status or {}).items():
        vid = k.decode() if isinstance(k, (bytes, bytearray)) else k
        raw = v.decode() if isinstance(v, (bytes, bytearray)) else v
        try:
            entry = json.loads(raw)
        except Exception:
            entry = {}
        if entry.get("success"):
            completed_ids.append(vid)
            for key, val in entry.items():
                if key in ("success", "error") or not isinstance(val, (int, float)):
                    continue
                numeric_totals[key] = numeric_totals.get(key, 0) + val
        else:
            failed_ids.append(vid)
    if total is None:
        # extract_videos hasn't finished dispatching — this phase hasn't
        # even started from the poller's point of view.
        return {"state": "PENDING", "meta": {}}
    state = "SUCCESS" if finished >= total and total > 0 else "PROGRESS"
    if total == 0:
        # Nothing was ever dispatched to this phase (e.g. every video
        # failed/had no transcript) — trivially done.
        state = "SUCCESS"
    if phase == "qdrant" and state == "SUCCESS":
        # 2026-09-14: the per-video counter reaching `total` happens the
        # INSTANT the last video's `mark_video_done` call returns — which
        # is BEFORE `ingestion.service._flush_buffer`'s drain of
        # whatever's still buffered even STARTS, let alone finishes.
        # Reporting SUCCESS at that instant showed a "Done" bar while a
        # slow embedding call was still running, with no signal that
        # stopping the run would silently kill it mid-write (observed
        # live: a Stop aimed at the unrelated Neo4j phase SIGTERM'd an
        # in-flight drain because Stop revokes every tracked task for
        # the run). Downgrade back to PROGRESS while the drain flag
        # (`ingestion.keys.qdrant_draining_key`) is still set — it
        # self-expires (120s TTL) so a hard-killed drain can't wedge
        # the bar at PROGRESS forever.
        try:
            if await redis.get(domains.ycs.ingestion.keys.qdrant_draining_key(extract_id)):
                state = "PROGRESS"
        except Exception:
            pass
    if phase == "neo4j" and state == "SUCCESS":
        # 2026-09-15: mirrors the qdrant-draining downgrade above —
        # `finished >= total` flips the instant the last video/
        # partition-group's `mark_video_done` returns, but
        # `entities_merged` is only written AFTER the whole-graph
        # `resolve_entities` pass finishes (tens of seconds later on a
        # large graph). Without this, a poller that stops on SUCCESS
        # freezes the displayed merge count at 0 forever.
        try:
            if await redis.get(keys.neo4j_resolving_key(extract_id)):
                state = "PROGRESS"
        except Exception:
            pass
    # 2026-09-16: piece-granular twin of the video-level preview count
    # below — see the `piece_current` computation further down for why
    # this needs its own count instead of reusing `len(completed_ids)`.
    preview_piece_count = 0
    if state == "PROGRESS":
        # 2026-09-14: union the display-only in-chunk preview (written
        # by chunked phase tasks as videos complete INSIDE the chunk)
        # into the DISPLAYED completed/current — chunk tasks only call
        # `mark_video_done` when the whole chunk lands, so without this
        # the bar sits at 0/N while videos are visibly succeeding in
        # the logs. Never touches `finished`/`total`/finalize.
        try:
            # SMEMBERS, not GET+json.loads — see update_phase_preview's
            # docstring (SADD replaced a JSON-list overwrite that
            # clobbered concurrent chunks' progress).
            raw_members = await redis.smembers(keys.phase_preview_key(extract_id, phase))
            if raw_members:
                # 2026-09-16: `raw_members` is ALREADY piece-granular —
                # `neo4j_task/task.py` populates it from
                # `collect(DISTINCT d.video_id)`, and a split video's
                # `Document` nodes are one per PARTITION, not one per
                # parent. So its own count is directly the piece-level
                # analog of `len(completed_ids)` below — no further
                # dedup/grouping needed, unlike `completed_ids` (which
                # stays video-level and CAN mix parent + partition ids
                # here, a pre-existing display-only quirk out of scope
                # for this fix).
                preview_piece_count = len(raw_members)
                known = set(completed_ids) | set(failed_ids)
                for raw_vid in raw_members:
                    vid = raw_vid.decode() if isinstance(raw_vid, (bytes, bytearray)) else raw_vid
                    if vid and vid not in known:
                        completed_ids.append(vid)
                        known.add(vid)
        except Exception:
            pass
    return {
        "state": state,
        "meta": {
            "phase":         "streaming",
            "current":       (min(max(finished, len(completed_ids)), total)
                              if state == "PROGRESS" else min(finished, total)),
            "total":         total,
            "completed_ids": completed_ids,
            "failed_ids":    failed_ids,
            # 2026-09-15: PIECE-level (partitions counted individually)
            # counterpart to current/total above — only present once
            # `extract_videos` has corrected the piece total (see
            # `keys.phase_piece_total_key`). The Neo4j bar prefers this
            # pair when present so a split video reads as "5/5 pieces"
            # instead of "2/2 videos", which understated how much
            # per-piece LLM extraction work the phase actually did.
            #
            # 2026-09-16 fix: `piece_current` used to be the RAW
            # `phase_piece_finished_key` counter only, with no preview
            # boost — but `phase_piece_finished_key` only increments
            # when a chunk task's FINAL reporting loop runs (after
            # every video in that chunk finishes, success or exhausted
            # retries), same as `finished` above. A live 5-video run
            # showed this exact gap: chunk `[YVj5yETvEl0, -mjwZ-hM_Y0]`
            # had `-mjwZ-hM_Y0` visibly done (in the preview set,
            # boosting `current` to 4/5) while `YVj5yETvEl0` was still
            # retrying — so the whole chunk's reporting loop hadn't run
            # yet, leaving `piece_finished` at 3 and the bar reading
            # "0%"/"0/5 pieces" while the video-level bar correctly
            # showed progress. Mirrors `current`'s own
            # `max(finished, len(completed_ids))` pattern exactly.
            **({
                "piece_current": (
                    min(max(piece_finished, preview_piece_count), piece_total)
                    if state == "PROGRESS" else
                    min(piece_finished, piece_total)
                ),
                "piece_total":   piece_total,
            } if piece_total is not None else {}),
        },
        "result": {
            "completed_ids":   completed_ids,
            "failed_ids":      failed_ids,
            "total_transcripts": len(completed_ids),
            **numeric_totals,
            **({
                "piece_current": min(piece_finished, piece_total),
                "piece_total":   piece_total,
            } if piece_total is not None else {}),
        } if state == "SUCCESS" else None,
    }
