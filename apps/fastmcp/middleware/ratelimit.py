"""RateLimitMiddleware — per-tool minimum-interval gate.

Replaces the in-tool `_AsyncRateLimiter` that used to live in
domains/rr/tools/arxiv/service.py. Lifting it here means every future tool
inherits the same async token-bucket for free — they just declare their
interval from `tool.py`'s register() function via `ratelimit.register(...)`.

Process-local — one gate per (process, tool_name). When the project scales
horizontally beyond one fastmcp replica, swap the per-tool dicts here for
Redis-backed leaky-bucket counters (Sentinel-substrate v2).

Also provides a per-tool circuit breaker (`trip_breaker` / `breaker_tripped`)
for upstream APIs that load-shed with a non-retryable-immediately signal
(e.g. arXiv's bare 406 on overload — doesn't clear on instant retry). A
tool calls `trip_breaker(name, cooldown_s)` itself once it's exhausted its
own retry budget; future calls to that tool check `breaker_tripped(name)`
and fail fast instead of spending a live request re-discovering the same
overload. Opt-in per tool — nothing here calls these automatically.

API:
  ratelimit.register("arxiv_search", 3.0)   # called by each tool at register
  ratelimit.trip_breaker("arxiv_search", 120.0)   # called by the tool on exhausted retries
  ratelimit.breaker_tripped("arxiv_search")       # -> seconds remaining, or None
  RateLimitMiddleware()                     # installed once in server.py
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext


logger = logging.getLogger(__name__)


# Per-tool min-interval registry. Mutated only at import-time (each tool's
# register() function calls `register(name, interval_s)`); read at call time.
_intervals: dict[str, float] = {}
_last_t: dict[str, float] = {}
_lock = asyncio.Lock()

# Per-tool circuit breaker — tool_name -> monotonic timestamp the breaker
# clears at. Plain `time.monotonic()` (not tied to a running event loop)
# since trip/check can happen from different call sites.
_breaker_until: dict[str, float] = {}


def register(tool_name: str, min_interval_s: float) -> None:
    """Declare a tool's minimum interval between requests (seconds).

    Called once at tool registration. Overwrites prior registration silently
    (re-import is idempotent). Tools with no registration are NOT rate-limited.
    """
    _intervals[tool_name] = float(min_interval_s)
    logger.info(
        f"[ratelimit] {tool_name!r} registered min_interval_s={min_interval_s}"
    )


async def _wait_for_slot(tool_name: str) -> None:
    interval_s = _intervals.get(tool_name, 0.0)
    if interval_s <= 0:
        return
    async with _lock:
        loop = asyncio.get_running_loop()
        last = _last_t.get(tool_name, 0.0)
        elapsed = loop.time() - last
        if elapsed < interval_s:
            await asyncio.sleep(interval_s - elapsed)
        _last_t[tool_name] = loop.time()


def trip_breaker(tool_name: str, cooldown_s: float) -> None:
    """Record that `tool_name`'s upstream just load-shed us. Calls to
    `breaker_tripped(tool_name)` return the remaining cooldown until this
    clears."""
    _breaker_until[tool_name] = time.monotonic() + cooldown_s
    logger.warning(f"[ratelimit] {tool_name!r} circuit breaker tripped for {cooldown_s:.0f}s")


def breaker_tripped(tool_name: str) -> float | None:
    """Seconds remaining until `tool_name`'s breaker clears, or None if
    it isn't tripped (never tripped, or the cooldown already elapsed)."""
    until = _breaker_until.get(tool_name)
    if until is None:
        return None
    remaining = until - time.monotonic()
    if remaining <= 0:
        del _breaker_until[tool_name]
        return None
    return remaining


class RateLimitMiddleware(Middleware):
    """Wait out the per-tool min-interval BEFORE the tool body runs."""

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> Any:
        try:
            tool_name = context.message.name
        except Exception:
            tool_name = "unknown"
        await _wait_for_slot(tool_name)
        return await call_next(context)
