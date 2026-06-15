"""Tunables for RR runtime — Redis timeouts + SSE snapshot retention.

Per docs/CODE-CONVENTIONS.md §3: loose numeric tunables live in params.py.
"""
from __future__ import annotations


# Redis client socket timeouts — short enough that a Redis blip surfaces
# fast instead of stalling a whole agent run.
REDIS_CONNECT_TIMEOUT_S: float = 3.0
REDIS_OP_TIMEOUT_S:      float = 5.0


# Snapshot list — bounded so a long scan doesn't grow Redis memory; TTL
# so a finished scan auto-evicts. Both numbers chosen to comfortably
# cover the agent run (1800 s soft-limit + 10× headroom).
SNAPSHOT_MAX_EVENTS: int = 500
SNAPSHOT_TTL_S:      int = 6 * 60 * 60     # 6 hours


# SSE subscriber poll cadence — short enough that live events feel
# instant, long enough to let asyncio breathe.
SSE_POLL_INTERVAL_S: float = 0.5


# Task-id store — Celery task UUID held under `rr:{scan_id}:task_id` so
# `POST /scan/{id}/cancel` can resolve scan_id → task_id and revoke. TTL
# matches the snapshot retention so cancel is reachable for the same
# window the SSE replay covers.
TASK_ID_TTL_S: int = SNAPSHOT_TTL_S
