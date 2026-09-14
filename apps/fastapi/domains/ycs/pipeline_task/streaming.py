"""ycs/pipeline_task — per-video streaming coordination (Imperative Shell).

2026-09-13: the per-video streaming fan-out (Playwright/ES → Neo4j +
Qdrant starting per video, not after the whole batch) replaces the
single "one Celery task per phase" model with N independent per-video
tasks for Neo4j and N for Qdrant. That breaks the assumption every
other piece of this pipeline was built on — that `neo4j`/`qdrant` are
each ONE task whose completion is directly observable. This module is
the replacement completion model: atomic Redis counters + an exactly-
once finalize trigger, instead of Celery's own chain/chord machinery
(which requires the full task set to be known at canvas-build time —
true here for the total COUNT, but not for WHEN each one should start,
which is the entire point of streaming them as videos complete).

Exactly-once finalize, without a lock on the hot path:
  1. `extract_videos` sets `phase_total_key(id, phase)` to the exact
     count of videos it dispatched to that phase, once — after its own
     dispatch loop finishes (this is the ONLY point that count is
     ever known).
  2. Every per-video task, on completion, calls `mark_video_done`,
     which does an atomic `INCR` on `phase_finished_key`. Because INCR
     is atomic and strictly monotonic, at most ONE caller's INCR can
     ever return any given integer — so "did MY increment reach the
     total" is a race-free way to detect "I am the last one," with no
     lock needed for this check itself.
  3. Whichever caller (a per-video task, or `extract_videos` itself,
     covering the rare case where every video finishes before the
     total is even set) observes BOTH phases' finished == total takes
     the `finalize_flag_key` via `SET ... NX` — exactly one caller can
     ever win that, regardless of how many observe the condition true
     at once — and is the one that fires `invalidate_cache`.

All Redis keys share `PIPELINE_STATE_TTL_S` so a crashed run's
bookkeeping doesn't leak forever."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis.asyncio as redis_aio

from .keys import (
    dispatched_tasks_key,
    finalize_flag_key,
    phase_finished_key,
    phase_status_key,
    phase_total_key,
)
from .params import PIPELINE_STATE_TTL_S


logger = logging.getLogger(__name__)

PHASES = ("neo4j", "qdrant")


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
) -> None:
    """Called exactly once per phase, by `extract_videos`, after its
    dispatch loop finishes. `total` is the count of videos that
    actually got a downstream task fired (cached-transcript videos
    still count — they get a per-video task same as freshly-fetched
    ones; only permanently-failed/no-transcript videos are excluded,
    since those never produce an ES doc for downstream to read)."""
    await redis.set(phase_total_key(extract_id, phase), total, ex = PIPELINE_STATE_TTL_S)


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
            phase_status_key(extract_id, phase), video_id, json.dumps(payload),
        )
        await redis.expire(phase_status_key(extract_id, phase), PIPELINE_STATE_TTL_S)
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] status write failed for "
            f"{phase}/{video_id}: {type(e).__name__}: {e}"
        )
    finished = await redis.incr(phase_finished_key(extract_id, phase))
    await redis.expire(phase_finished_key(extract_id, phase), PIPELINE_STATE_TTL_S)
    raw_total = await redis.get(phase_total_key(extract_id, phase))
    total = int(raw_total) if raw_total is not None else None
    return int(finished), total


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
        raw = await redis.hget(phase_status_key(extract_id, phase), video_id)
        entry = json.loads(raw) if raw else {}
        entry.update(extra)
        await redis.hset(phase_status_key(extract_id, phase), video_id, json.dumps(entry))
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] status merge failed for "
            f"{phase}/{video_id}: {type(e).__name__}: {e}"
        )


async def track_dispatched_task(
    redis: redis_aio.Redis, extract_id: str, task_id: str,
) -> None:
    """Best-effort record of a fired per-video task id so Stop can
    revoke everything actually in flight, not just a fixed 4 ids."""
    try:
        await redis.rpush(dispatched_tasks_key(extract_id), task_id)
        await redis.expire(dispatched_tasks_key(extract_id), PIPELINE_STATE_TTL_S)
    except Exception as e:
        logger.warning(
            f"[ycs:pipeline:streaming] dispatched-task tracking failed "
            f"for {task_id}: {type(e).__name__}: {e}"
        )


async def get_dispatched_task_ids(
    redis: redis_aio.Redis, extract_id: str,
) -> list[str]:
    try:
        raw = await redis.lrange(dispatched_tasks_key(extract_id), 0, -1)
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
        raw_total = await redis.get(phase_total_key(extract_id, phase))
        if raw_total is None:
            return False  # extract_videos hasn't finished dispatching yet
        raw_finished = await redis.get(phase_finished_key(extract_id, phase))
        finished = int(raw_finished) if raw_finished is not None else 0
        if finished < int(raw_total):
            return False
    got_lock = await redis.set(
        finalize_flag_key(extract_id), "1", nx = True, ex = PIPELINE_STATE_TTL_S,
    )
    if not got_lock:
        return False  # another caller already finalized this run
    logger.info(
        f"[ycs:pipeline:streaming] {extract_id}: both phases complete, "
        f"finalizing (invalidate_cache)"
    )
    from domains.ycs.qdrant_task.task import invalidate_cache
    invalidate_cache.delay()
    return True


async def get_phase_progress(
    redis: redis_aio.Redis, extract_id: str, phase: str,
) -> dict[str, Any]:
    """Synthesize the `{state, meta}` shape `/admin/task/{id}` normally
    returns, from this module's Redis counters/status hash, so the
    FastAPI aggregator endpoint can hand the FastHTML poller something
    shaped exactly like what it already knows how to render."""
    raw_total = await redis.get(phase_total_key(extract_id, phase))
    total = int(raw_total) if raw_total is not None else None
    raw_finished = await redis.get(phase_finished_key(extract_id, phase))
    finished = int(raw_finished) if raw_finished is not None else 0
    try:
        raw_status = await redis.hgetall(phase_status_key(extract_id, phase))
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
    return {
        "state": state,
        "meta": {
            "phase":         "streaming",
            "current":       min(finished, total),
            "total":         total,
            "completed_ids": completed_ids,
            "failed_ids":    failed_ids,
        },
        "result": {
            "completed_ids":   completed_ids,
            "failed_ids":      failed_ids,
            "total_transcripts": len(completed_ids),
            **numeric_totals,
        } if state == "SUCCESS" else None,
    }
