"""Tunable scalars shared across the pipeline's run machinery."""
from __future__ import annotations


CANCEL_TTL_S = 3600
SNAPSHOT_TTL_S = 3600
# A study can run for hours and a page reload must find its progress: the synth keeps its event snapshot a day.
SYNTH_SNAPSHOT_TTL_S = 86400
SNAPSHOT_MAX_EVENTS = 200

# Per-slug single-flight lock: just above the Celery task time limit, so a crashed run self-releases.
LOCK_TTL_S = 3660
# …and the synth's: just above the study task's hard limit.
SYNTH_LOCK_TTL_S = 21900
# Live-run registry (page-refresh recovery) — see `runtime.dispatch.service.register_active_run`.
ACTIVE_RUN_TTL_S = 3600
ACTIVE_STUDY_TTL_S = 14400
# The set of a study's in-flight chapter threads (cancel propagation).
ACTIVE_CHAPTERS_TTL_S = 86400


def snapshot_ttl_s(thread_id: str) -> int:
    """How long a thread's progress snapshot lives, by its stage."""
    from . import keys
    return SYNTH_SNAPSHOT_TTL_S if keys.stage_of(thread_id) == "synth" else SNAPSHOT_TTL_S
