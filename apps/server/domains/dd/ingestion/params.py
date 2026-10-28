from __future__ import annotations


# The Celery task that runs an ingestion: it is warned after SOFT_TIME_LIMIT_S and killed after TIME_LIMIT_S. The per-framework lock is
# a short lease the run keeps renewing (`runtime.progress.params.LOCK_LEASE_S`), so a task killed at either limit — or by a redeploy —
# frees the framework within one lease.
TASK_NAME = "domains.dd.ingestion.task.run_ingestion"
SOFT_TIME_LIMIT_S = 3600
TIME_LIMIT_S = 3660
