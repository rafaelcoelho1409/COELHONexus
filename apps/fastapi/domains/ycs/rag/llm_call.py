"""ycs/rag — resilient single-LLM-call helper for every Ask node.

Mirrors the Docs Distiller Planner engine (`doc_distill` + `chat_judge_bandit_async`):
retry only genuinely transient errors (timeout/connection — never rate_limit,
which means the provider side is benched and a retry just burns budget),
with jittered backoff so concurrent retries don't herd onto the same arm.

Structure here is one layer simpler than DD's on purpose: every Ask node
already wraps its call in `asyncio.wait_for(..., timeout=node_timeout)` where
`node_timeout` (30–600s) sits well under the SDK client's own 600s ceiling —
so that wait_for IS the hard backstop over the SDK timeout (the httpx-gap
fix: httpx read-timeout measures inter-chunk gaps, not total call time; a
trickling or silently-stalled connection outlives it without raising, but it
cannot outlive this wait_for). This helper adds the missing piece DD has and
Ask lacked: transparent retries *inside* that bound.

Drop-in contract: `resilient_ainvoke(chain, payload, ...)` raises the LAST
exception unchanged after exhausting attempts — so each node's existing
`except (asyncio.TimeoutError, Exception)` handling behaves exactly as today,
only now transient blips get retried first instead of surfacing immediately.
Non-transient errors (validation, auth, rate_limit, parse) raise on the
first attempt with zero extra waiting.
"""
from __future__ import annotations

import asyncio
import logging
import random

logger = logging.getLogger(__name__)

# DD parity: these mean "try again" — same rationale as
# `doc_distill._TRANSIENT_REASONS` (timeout/connection only).
_TRANSIENT_SUBSTRINGS = (
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

# Checked FIRST — these must never be retried: the provider side already
# exhausted its own cascade (retrying burns seconds for an identical
# outcome — see doc_distill's removal of rate_limit from retryables).
_NON_TRANSIENT_SUBSTRINGS = (
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


def is_transient(exc: BaseException) -> bool:
    """True only for errors worth spending another attempt on."""
    if isinstance(exc, asyncio.TimeoutError):
        return True
    msg = str(exc).lower()
    if any(k in msg for k in _NON_TRANSIENT_SUBSTRINGS):
        return False
    return any(k in msg for k in _TRANSIENT_SUBSTRINGS)


async def resilient_ainvoke(
    chain,
    payload: dict,
    *,
    operation:    str,
    timeout_s:    float,
    max_attempts: int             = 3,
    backoff_s:    tuple[float, ...] = (2.0, 5.0),
):
    """`await asyncio.wait_for(chain.ainvoke(payload), timeout_s)` with
    transient-only retries inside the bound. Raises the last exception
    unchanged when attempts run out (or immediately for non-transient)."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(
                chain.ainvoke(payload),
                timeout = timeout_s,
            )
        except Exception as e:  # noqa: BLE001 — classified below
            last_exc = e
            if not is_transient(e) or attempt >= max_attempts:
                break
            delay = backoff_s[min(attempt - 1, len(backoff_s) - 1)]
            delay *= 1.0 + random.random() * 0.2  # DD-parity jitter
            logger.warning(
                f"[ycs:rag:{operation}] transient "
                f"{type(e).__name__} (attempt {attempt}/{max_attempts}) — "
                f"retrying in {delay:.1f}s",
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
