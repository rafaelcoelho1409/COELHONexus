"""Celery tunables — Redis URL, env-scoped queues, task discovery + routing.

Hard reads (no defaults): a worker without broker config must fail fast
at import, not boot deaf (`infra.redis.service.url()` raises when
`REDIS_HOST` is unset). This is also why `celery/` stays out of
`infra/__init__.py`'s eager chain.
"""
from __future__ import annotations
import infra

import os


REDIS_URL = infra.redis.service.url()


# ENVIRONMENT-suffixed queues so dev/prod don't steal each other's tasks
# on the shared Redis broker (see keys.py — the queue names themselves
# live there, scoped via domain.scoped_queue).
ENVIRONMENT = os.environ["ENVIRONMENT"].lower()


# App-config numerics (were inline in service.py's config dict).
RESULT_EXPIRES_S = 86400
WORKER_PREFETCH_MULTIPLIER = 1
EVENT_QUEUE_EXPIRES_S = 60.0
