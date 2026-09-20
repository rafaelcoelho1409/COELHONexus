"""Tunables for RR runtime — Redis timeouts, SSE snapshot retention,
extraction-cache TTL, resilient-call retry classification.

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


# Extraction cache (Wave 1.7, 2026-06-16) — bump PROMPT_VERSION when the
# deep_read system prompt or 5-field rubric materially changes; old
# cached extractions remain under their old version key and naturally
# expire via TTL. Cheap to bump; no purge needed.
EXTRACTION_PROMPT_VERSION: str = "v1"

# 7 days — matches the radar's typical scan cadence (operator runs the
# same topic weekly to track new releases). Long enough for repeat
# scans to benefit, short enough that prompt-version churn doesn't
# leak stale data forever.
EXTRACTION_TTL_S: int = 7 * 24 * 3600


# fs mirror — reuses the snapshot TTL (same scope as the SSE replay
# window; long enough that the drawer can introspect any scan reachable
# via its URL).
FS_MIRROR_TTL_S: int = SNAPSHOT_TTL_S


# Code synth status — the Build tab's generate→critique→revise loop
# usually takes 1-5 min but can run longer under provider load; TTL is a
# crash-safety net (a worker that dies mid-run without clearing the key)
# rather than the expected lifetime, so it's set well above the observed
# ceiling. Error entries use the same TTL — short enough would just mean
# a stale error silently reopens the idle "Generate" button, which is a
# safe failure mode.
CODE_SYNTH_STATUS_TTL_S: int = 20 * 60     # 20 minutes


# resilient_ainvoke retry classification — same DD/YCS-proven
# transient/non-transient split, kept identical so RR's retry behavior
# matches the rest of the app.
TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "reset by peer",
    "connection closed",
    "connection refused",
    "unreachable",
    "temporarily unavailable",
    "service unavailable",
    "internal error",
    "overloaded",
)

NON_TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "429",
    "rate limit",
    "ratelimit",
    "quota",
    "throttle",
    "resource exhausted",
    "permission",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "not found",
    "context length",
    "maximum context",
    "content filter",
    "moderation",
)
