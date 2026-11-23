"""Postgres connection env + checkpointer pool tunables.

Soft env reads: `service.check_configured()` raises one clear error naming the
missing variables, and `url()` / `connect()` call it before connecting."""
from __future__ import annotations

import os


HOST     = os.environ.get("POSTGRES_HOST", "")
PORT     = os.environ.get("POSTGRES_PORT", "5432")
USER     = os.environ.get("POSTGRES_USER", "")
PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")

# Database every domain uses (set by the Helm chart): persisted workflow checkpoints and YCS conversation state.
DATABASE = os.environ.get("POSTGRES_DATABASE", "")

# LangGraph checkpointer pool.
POOL_MIN_SIZE        = 1
POOL_MAX_SIZE        = 4
POOL_OPEN_TIMEOUT_S  = 30.0
CHECKPOINT_LOOKUP_RETRY_ATTEMPTS = 3
CHECKPOINT_LOOKUP_RETRY_BACKOFF_S = 0.5
