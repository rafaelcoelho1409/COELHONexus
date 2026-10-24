from __future__ import annotations


TTL_S = 7200

# Outlives Celery soft_time_limit (3600 s) so crashes self-release.
LOCK_TTL_S = 3900

THROTTLE_S = 1.0
CANCEL_POLL_THROTTLE_S = 1.0
