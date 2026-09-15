"""ycs/pipeline_task — Redis key builder for pipeline dispatch state.

Per `docs/CODE-CONVENTIONS.md` §2, storage-path helpers belong in
`keys.py`. Single source of truth for the
`PIPELINE_STATE_PREFIX + <extract_task_id>` shape so producers
(`service.persist_pipeline_state`) and consumers
(`service.load_pipeline_state`) agree without trafficking string
literals.

2026-09-13: added the per-video-streaming key family (`streaming.py`'s
exactly-once finalize + per-video status tracking). All keyed by the
SAME `extract_id` used above, under the `PIPELINE_STATE_PREFIX`
namespace, so one TTL sweep policy covers everything for a run."""
from __future__ import annotations

from .params import PIPELINE_STATE_PREFIX


def pipeline_state_key(extract_id: str) -> str:
    """Redis key storing the dispatch params (`video_ids`, flags) for
    one Videos-tab chain. Keyed by the Phase A (extract) task id so the
    FastHTML poller's URL (`?extract=<id>`) is the lookup token."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}"


def phase_total_key(extract_id: str, phase: str) -> str:
    """Total video count dispatched to `phase` ('neo4j' | 'qdrant') for
    this run. Set once, by `extract_videos`, after the last video's
    downstream dispatch — before that, reads as unset (`None`), which
    `maybe_finalize` treats as 'extraction still in flight, don't
    finalize yet.'"""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:total"


def phase_finished_key(extract_id: str, phase: str) -> str:
    """Atomic counter — INCR'd once per video by that video's per-video
    `phase` task on completion (success or failure). The task whose
    INCR call returns exactly the value in `phase_total_key` is,
    exactly once, the one that observes 'this phase is done.'"""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:finished"


def phase_status_key(extract_id: str, phase: str) -> str:
    """Redis HASH `{video_id: json(status_payload)}` — per-video state
    for `phase`, read back by the FastAPI aggregator endpoint so the
    FastHTML poller can render a bar + drawer row for phases that no
    longer map to one Celery task id."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:status"


def finalize_flag_key(extract_id: str) -> str:
    """`SETNX` guard — whichever caller sets this first is, exactly
    once, the one that fires `invalidate_cache` for this run."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:finalized"


def dispatched_tasks_key(extract_id: str) -> str:
    """Redis LIST of every per-video Neo4j/Qdrant Celery task id fired
    for this run, so the Stop button can revoke all of them — a static
    4-id list no longer covers what's actually in flight once
    downstream work fans out per video."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:dispatched_tasks"


def partition_group_key(extract_id: str, phase: str, parent_video_id: str) -> str:
    """Redis HASH `{partition_id: json(outcome)}` — per-parent-video
    tracker for long-video partitions (`"XYZ#p1".."XYZ#pN"`). A
    partition finishing `phase` records here instead of touching
    `phase_finished_key` directly; only once every expected partition
    has reported does `mark_video_or_partition_done` fire ONE
    aggregated `mark_video_done` call for `parent_video_id` — so a
    5-partition video counts as exactly 1 toward the phase total, the
    same as any other video, and the drawer row for the original id
    reports done/failed based on the whole group, not one partition."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:partgroup:{parent_video_id}"


def pipeline_cancel_key(extract_id: str) -> str:
    """Cooperative-cancel flag — Stop sets this instead of relying only
    on `celery.control.revoke(terminate=True)`. `extract_videos`'
    Playwright chunk loop and `ingest_to_neo4j`'s retry-pass loop poll
    it at safe checkpoints (chunk/pass boundaries, never mid-await) and
    exit early on their own when it's set.

    2026-09-14: added after a live SIGTERM-revoke wedged Celery's
    prefork pool — killing a task running Playwright's Node driver
    subprocess left the worker slot's OS process idle but the pool's
    own bookkeeping still marked it busy, so every task dispatched
    after sat in `reserved` forever. Cooperative self-exit sidesteps
    that failure mode entirely for the common "user clicked Stop"
    case; `revoke_pipeline_phases` (non-terminating) still discards
    anything not yet started."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:cancel"


def phase_piece_total_key(extract_id: str, phase: str) -> str:
    """Total individual PIECES (partitions + unsplit videos) dispatched
    to `phase`, distinct from `phase_total_key`'s VIDEO-level count. A
    video split into 4 partitions counts as 1 toward `phase_total_key`
    (so admin listing / finalize / cross-phase checks stay video-
    scoped — the whole point of `mark_video_or_partition_done`) but as
    4 here.

    2026-09-15: added so the Neo4j bar can show "K/M pieces" instead of
    "K/N videos" — LLM extraction concurrency/cost is piece-scoped, so
    K/N badly understated how much work was happening (2 videos / 5
    pieces displayed identically to "2/2 done" either way, live-
    observed as confusing after a 4-partition long video)."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:piece_total"


def phase_piece_finished_key(extract_id: str, phase: str) -> str:
    """Atomic counter — INCR'd once per PIECE (every
    `mark_video_or_partition_done` call, whether or not it completes a
    partition group) — pairs with `phase_piece_total_key`."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:piece_finished"


def neo4j_resolving_key(extract_id: str) -> str:
    """2026-09-15: set while the run's ONE post-streaming entity-resolution
    pass (`resolve_entities`, rapidfuzz cross-reference dedup over the
    whole graph) is in flight. `phase_finished_key` for "neo4j" reaches
    its total the INSTANT the last video/partition-group reports —
    before resolution even starts, since `entities_merged` is only
    known after it finishes (observed live: 858 nodes/1764 rels showed
    correctly the instant the phase hit SUCCESS, but `entities_merged`
    stayed 0 because resolution was still running ~60s+ after that).
    `get_phase_progress` downgrades neo4j's SUCCESS back to PROGRESS
    while this is set — same pattern as
    `ingestion.keys.qdrant_draining_key` for Qdrant's post-total drain.
    Self-expiring TTL so a crash mid-resolution can't wedge the bar."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:neo4j:resolving"


def phase_preview_key(extract_id: str, phase: str) -> str:
    """2026-09-14: display-only in-progress preview for chunked phases.
    Neo4j dispatches CHUNKS (up to 5 videos per Celery task) but the
    bar/drawer poll per-video state — without this, the bar sits at
    0/N until the whole chunk lands. Chunk tasks overwrite this key
    with their cumulative `completed_ids` as videos finish inside the
    chunk; `get_phase_progress` unions it into the DISPLAYED
    completed/current (never into the finalize counters)."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:preview"
