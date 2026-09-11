"""Tunable scalars shared across the synth package."""
from __future__ import annotations

import os


CANCEL_TTL_S = 3600
SNAPSHOT_TTL_S = 86400   # 24h — covers overnight reloads while a study run is in flight
SNAPSHOT_MAX_EVENTS = 200

REDIS_CONNECT_TIMEOUT_S = 3.0
REDIS_OP_TIMEOUT_S = 5.0


CHECKLIST_THRESHOLD = 0.80
MAX_REFINE_ITER = 5
PLATEAU_DELTA = 0.03

# chapters below 0.5 at iter-1 rarely recover above 0.80; best-seen rescue at iter-1 ≈ iter-2
NO_RECOVERY_FLOOR = 0.50

# Consecutive RETHINK iterations whose low score was infra-driven (judge/
# CoCoA/atomic-claim call failures, not genuine content review) before
# halting early instead of burning the full MAX_REFINE_ITER budget against
# an outage that isn't clearing. Confirmed live: 3+ in a row produced zero
# improvement over ~17 extra minutes of compute. Unbenchmarked threshold —
# tune against real study runs, not a measured optimum.
SUSTAINED_INFRA_OUTAGE_LIMIT = 2

# Single-chapter Celery task limits — task.py's run_single_chapter /
# resume_synth decorators import these (single source of truth). Widened
# 2026-09-11: the prior 60s soft→hard gap (3600/3660) was below Celery's own
# documented minimum (300s) for a task to reliably clean up after the soft
# limit fires.
SINGLE_CHAPTER_SOFT_TIME_LIMIT_S = 3600.0
SINGLE_CHAPTER_HARD_TIME_LIMIT_S = 3900.0

# Wall-clock RETHINK gate (2026-09-11 incident): a chapter-01 run against a
# thinned pool burned the full soft_time_limit across ~1.5 RETHINK
# iterations and was hard-killed by Celery mid-sawc_write with ZERO output —
# _route_after_mgsr's existing HALT checks (score/iter-count/
# consecutive_infra_degraded) never got a chance to fire because the kill
# happened *inside* a node, not at a graph decision boundary. Before looping
# back into another sawc_write iteration, halt to best-seen-rescue instead
# if the remaining budget can't plausibly fit one — estimated from the last
# sawc_write iteration's own measured wall_ms (state["sawc_stats"]["wall_ms"])
# times this safety margin.
WALL_CLOCK_SAFETY_MARGIN = 1.15
# No prior measurement to estimate from (shouldn't happen past iter 1, but
# defensive): assume the worst cost actually observed live (~28 min).
FALLBACK_SAWC_WRITE_COST_S = 1700.0


# API-bound on K8s; SEM=2 doubles throughput without contention (book_harmonize post-serializes). KD_STUDY_SEM rolls back to 1.
STUDY_SEM = int(os.environ["KD_STUDY_SEM"])


# ~8 keeps MinIO+local-CPU saturated without flooding (parse+hash bound, not I/O)
BACKFILL_CONCURRENCY = 8
