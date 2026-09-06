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


# API-bound on K8s; SEM=2 doubles throughput without contention (book_harmonize post-serializes). KD_STUDY_SEM rolls back to 1.
STUDY_SEM = int(os.environ["KD_STUDY_SEM"])


# ~8 keeps MinIO+local-CPU saturated without flooding (parse+hash bound, not I/O)
BACKFILL_CONCURRENCY = 8
