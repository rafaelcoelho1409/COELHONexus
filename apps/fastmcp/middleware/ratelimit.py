"""RateLimitMiddleware — per-tool minimum-interval gate.

Replaces the in-tool `_AsyncRateLimiter` that used to live in
domains/rr/tools/arxiv/service.py. Lifting it here means every future tool
inherits the same async token-bucket for free — they just declare their
interval from `tool.py`'s register() function via `ratelimit.register(...)`.

Process-local — one gate per (process, tool_name). When the project scales
horizontally beyond one fastmcp replica, swap the per-tool dicts here for
Redis-backed leaky-bucket counters (Sentinel-substrate v2).

API:
  ratelimit.register("arxiv_search", 3.0)   # called by each tool at register
  RateLimitMiddleware()                     # installed once in server.py
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext


logger = logging.getLogger(__name__)


# Per-tool min-interval registry. Mutated only at import-time (each tool's
# register() function calls `register(name, interval_s)`); read at call time.
_intervals: dict[str, float] = {}
_last_t: dict[str, float] = {}
_lock = asyncio.Lock()


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
