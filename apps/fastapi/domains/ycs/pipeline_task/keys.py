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


def phase_preview_key(extract_id: str, phase: str) -> str:
    """2026-09-14: display-only in-progress preview for chunked phases.
    Neo4j dispatches CHUNKS (up to 5 videos per Celery task) but the
    bar/drawer poll per-video state — without this, the bar sits at
    0/N until the whole chunk lands. Chunk tasks overwrite this key
    with their cumulative `completed_ids` as videos finish inside the
    chunk; `get_phase_progress` unions it into the DISPLAYED
    completed/current (never into the finalize counters)."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}:{phase}:preview"
