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


async def hedged_ainvoke(
    chain,
    payload: dict,
    *,
    operation:    str,
    timeout_s:    float,
    hedge_after_s: float = 20.0,
    max_invokes:  int   = 3,
    backoff_s:    tuple[float, ...] = (2.0, 5.0),
):
    """Tail-cutting racer for CHEAP calls (Tail-at-Scale pattern).

    Fires the primary immediately plus ONE delayed duplicate; slow tail
    (> `hedge_after_s`) gets raced, first success wins, loser cancelled
    — healthy path pays zero extra. A fast transient failure fires the
    duplicate immediately (retry semantics); non-transient raises at
    once. Total invokes capped at `max_invokes`, everything bounded by
    `timeout_s` from entry. Raises the last exception unchanged (same
    drop-in contract as `resilient_ainvoke`).

    Cost rule: only use where one extra short completion is affordable
    (fast-mode definitions). NEVER for heavy generate/synthesize calls —
    duplicating 12k-context completions is real money for unproven gain.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    fire_now = asyncio.Event()
    invokes = 0
    last_exc: BaseException | None = None

    async def _one() -> object:
        nonlocal invokes
        invokes += 1
        return await chain.ainvoke(payload)

    async def _delayed() -> object:
        try:
            await asyncio.wait_for(fire_now.wait(), timeout = hedge_after_s)
        except asyncio.TimeoutError:
            pass
        return await _one()

    async def _backoff(attempt: int) -> None:
        delay = backoff_s[min(attempt, len(backoff_s) - 1)]
        delay *= 1.0 + random.random() * 0.2
        logger.warning(
            f"[ycs:rag:{operation}] transient slow/fail — hedge "
            f"armed (invoke {invokes}/{max_invokes})"
        )
        await asyncio.sleep(delay)

    pending: set[asyncio.Task] = set()
    try:
        pending.add(asyncio.ensure_future(_one()))
        pending.add(asyncio.ensure_future(_delayed()))
        attempt = 0
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"[ycs:rag:{operation}] {timeout_s}s bound exceeded"
                )
            done, pending = await asyncio.wait(
                pending, timeout = remaining,
                return_when = asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError(
                    f"[ycs:rag:{operation}] {timeout_s}s bound exceeded"
                )
            for task in done:
                try:
                    result = task.result()
                except Exception as e:  # noqa: BLE001 — classified below
                    last_exc = e
                    if not is_transient(e):
                        for p in pending:
                            p.cancel()
                        raise
                    # Transient: fire the duplicate NOW (retry semantics)
                    # if budget remains, else keep waiting for the other.
                    fire_now.set()
                    if invokes < max_invokes and not pending:
                        await _backoff(attempt)
                        attempt += 1
                        pending.add(asyncio.ensure_future(_one()))
                    continue
                for p in pending:
                    p.cancel()
                return result
        assert last_exc is not None
        raise last_exc
    finally:
        fire_now.set()
        for task in pending:
            task.cancel()

