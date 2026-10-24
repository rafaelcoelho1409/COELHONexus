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
