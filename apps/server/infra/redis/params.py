"""Redis connection env + client timeouts.

Soft env reads: `service.url()` raises a clear error when `REDIS_HOST` is
missing, `service.is_configured()` lets optional callers (RR extraction
cache) skip Redis instead."""
from __future__ import annotations

import os


HOST     = os.environ.get("REDIS_HOST", "").strip()
PORT     = os.environ.get("REDIS_PORT", "6379").strip()
PASSWORD = os.environ.get("REDIS_PASSWORD", "").strip()

CONNECT_TIMEOUT_S = 3.0
OP_TIMEOUT_S      = 5.0

# Compare-and-delete — release a lock only if it still holds OUR owner id. Stops a slow
# `finally` from clearing a lock that a racing later start has taken.
RELEASE_IF_OWNER_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)
