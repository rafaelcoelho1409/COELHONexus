"""Postgres connection env + checkpointer pool tunables.

Soft env reads: `service.check_configured()` raises one clear error naming the
missing variables, and `url()` / `connect()` call it before connecting."""
from __future__ import annotations

import os


HOST     = os.environ.get("POSTGRES_HOST", "")
PORT     = os.environ.get("POSTGRES_PORT", "5432")
USER     = os.environ.get("POSTGRES_USER", "")
PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")

# Default database (set by the Helm chart). DD planner/synth checkpoints and RR use it.
DATABASE = os.environ.get("POSTGRES_DATABASE", "")

# YCS conversation + query-history tables live in this database. The chart never sets
# `POSTGRES_DB`, so in the cluster this is the `postgres` default, NOT `DATABASE` above.
YCS_DATABASE = os.environ.get("POSTGRES_DB", "postgres")

# LangGraph checkpointer pool (shared by planner + synth).
POOL_MIN_SIZE        = 1
POOL_MAX_SIZE        = 4
POOL_OPEN_TIMEOUT_S  = 30.0
