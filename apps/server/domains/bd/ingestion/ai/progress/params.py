from __future__ import annotations


SNAPSHOT_MAX_EVENTS = 5000
SNAPSHOT_TTL_S = 24 * 60 * 60

# A single run must have one Celery graph writer at a time. Workers renew this lease while the graph is active; a lost worker
# releases the run for a redelivered task within one lease window.
RUN_EXECUTION_LOCK_LEASE_S = 300
RUN_EXECUTION_LOCK_RENEW_S = 30
RUN_EXECUTION_LOCK_RETRY_S = 15
RUN_EXECUTION_LOCK_STALE_AFTER_S = RUN_EXECUTION_LOCK_LEASE_S + RUN_EXECUTION_LOCK_RENEW_S
