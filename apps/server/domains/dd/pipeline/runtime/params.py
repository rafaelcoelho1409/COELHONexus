"""Tunable scalars shared across the planner package."""
from __future__ import annotations


CANCEL_TTL_S = 3600
SNAPSHOT_TTL_S = 3600
SNAPSHOT_MAX_EVENTS = 200

# Per-slug single-flight lock: just above the Celery task time limit, so a crashed run self-releases.
LOCK_TTL_S = 3660
# Live-run registry (page-refresh recovery) — see `runtime.dispatch.service.register_active_run`.
ACTIVE_RUN_TTL_S = 3600
