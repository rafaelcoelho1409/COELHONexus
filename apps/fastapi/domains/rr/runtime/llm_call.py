"""rr/runtime — resilient single-LLM-call helper for callers outside the
DeepAgents loop (task.py's inline backfill, code_synth's 3-round
generate/critique/revise). The orchestrator + subagents get their retry
and usage-capture "for free" from DeepAgents' own turn loop + the
`RRLlmCounterCallback` attached to `agent.ainvoke(config=...)` in
task.py; these one-off `chain.ainvoke()` calls sit outside that loop
entirely and previously had neither.

Mirrors `domains.ycs.rag.service` (used by every YCS Ask node): retry
only genuinely transient errors (timeout/connection — never rate_limit,
which means the provider side already gave up and a retry just burns
budget), with jittered backoff so concurrent retries don't herd onto the
same arm. Drop-in contract: `resilient_ainvoke` raises the LAST exception
unchanged after exhausting attempts.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from .llm_counter import bump_llm_usage, get_phase, get_scan

logger = logging.getLogger(__name__)

# Same list YCS's llm_call.py uses — DD/YCS-proven transient/non-transient
# split, kept identical so RR's retry behavior matches the rest of the app.
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


async def capture_llm_usage(response: object) -> None:
    """Bump the per-scan Redis counters straight from a returned
    `AIMessage` — no-ops silently when no scan is in context (e.g.
    code_synth's router endpoint, which doesn't set one) or when the
    response carries no usage block."""
    try:
        scan_id = get_scan()
        if not scan_id:
            return
        usage = getattr(response, "usage_metadata", None) or {}
        tokens_in  = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        if not (tokens_in or tokens_out):
            return
        meta = getattr(response, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model") or "unknown"
        bump_llm_usage(
            scan_id    = scan_id,
            phase      = get_phase(),
            model      = model,
            tokens_in  = tokens_in,
            tokens_out = tokens_out,
        )
    except Exception as e:
        logger.warning(f"[rr:llm_call] usage capture failed: {type(e).__name__}: {e}")


async def resilient_ainvoke(
    chain: Any,
    messages: Any,
    *,
    operation:    str,
    timeout_s:    float,
    max_attempts: int               = 3,
    backoff_s:    tuple[float, ...] = (2.0, 5.0),
):
    """`await asyncio.wait_for(chain.ainvoke(messages), timeout_s)` with
    transient-only retries inside the bound. Raises the last exception
    unchanged when attempts run out (or immediately for non-transient)."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                chain.ainvoke(messages),
                timeout = timeout_s,
            )
            # 2026-09-17: these calls run outside the DeepAgents loop, so
            # `RRLlmCounterCallback`'s per-turn timing log never sees
            # them — log here instead, same shape, so a slow backfill/
            # code_synth call is diagnosable from `kubectl logs` alone.
            logger.info(
                f"[rr:llm_call:{operation}] duration_s="
                f"{time.monotonic() - t0:.1f} attempt={attempt}/{max_attempts}"
            )
            await capture_llm_usage(result)
            return result
        except Exception as e:  # noqa: BLE001 — classified below
            last_exc = e
            duration_s = time.monotonic() - t0
            if not is_transient(e) or attempt >= max_attempts:
                logger.warning(
                    f"[rr:llm_call:{operation}] FAILED after "
                    f"duration_s={duration_s:.1f} (attempt {attempt}/"
                    f"{max_attempts}, non-retryable or budget exhausted): "
                    f"{type(e).__name__}: {e}"
                )
                break
            delay = backoff_s[min(attempt - 1, len(backoff_s) - 1)]
            delay *= 1.0 + random.random() * 0.2
            logger.warning(
                f"[rr:llm_call:{operation}] transient "
                f"{type(e).__name__} after duration_s={duration_s:.1f} "
                f"(attempt {attempt}/{max_attempts}) — retrying in "
                f"{delay:.1f}s",
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
