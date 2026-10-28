"""Fetch a URL the way a crawler must: retried on timeouts, network errors and transient statuses, the server's Retry-After and rate-limit headers honoured, and — through a `Pacer` — one pace for all of a crawl's concurrent requests."""
from __future__ import annotations
from . import domain, params

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx


logger = logging.getLogger(__name__)


class Pacer:
    """Shared by every request of one crawl. When the site says "slow down" — a 429, or a rate-limit window with nothing
    left in it — `hold` has all of them wait together, so the crawl keeps to the site's limit instead of every worker
    finding it out for itself. `budget_s` bounds all the waiting a crawl does; `on_wait` is told, once per hold, how
    long the wait will be."""

    def __init__(
        self,
        *,
        budget_s: float = params.CRAWL_WAIT_BUDGET_S,
        on_wait: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._resume_at = 0.0
        self._budget_left = budget_s
        self._on_wait = on_wait
        self._announced_until = 0.0

    def hold(self, seconds: float) -> bool:
        """Have every request wait until `seconds` from now. False — and no wait — when what is left of the crawl's
        waiting budget cannot cover it."""
        now = time.monotonic()
        target = now + seconds
        extra = target - max(self._resume_at, now)
        if extra <= 0:
            return True
        if extra > self._budget_left:
            return False
        self._budget_left -= extra
        self._resume_at = target
        return True

    async def wait(self) -> None:
        """Return once no hold is in force (one that is extended while waiting is waited out too)."""
        while True:
            remaining = self._resume_at - time.monotonic()
            if remaining <= 0:
                return
            # Concurrent requests each ask for "the same" pause a moment apart: that is one hold, announced once.
            if self._on_wait is not None and self._resume_at > self._announced_until + params.ANNOUNCE_MERGE_S:
                self._announced_until = self._resume_at
                await self._on_wait(remaining)
            await asyncio.sleep(remaining)


async def get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    attempts: int,
    wait_initial: float,
    wait_max: float,
    retry_after_cap: float,
    pacer: Pacer | None = None,
) -> httpx.Response:
    """GET with retries on timeouts, network errors and transient statuses (429 / 5xx), the server's Retry-After
    honoured up to `retry_after_cap`. A transient status that outlasts the attempts comes back as the response — the
    caller decides what it means — while a timeout or network error that does raises. With a `pacer`, a site's "slow
    down" holds every request of the crawl, and a rate-limit window with nothing left in it is waited out before the
    next request spends a 429 on it."""
    for attempt in range(1, attempts + 1):
        if pacer is not None:
            await pacer.wait()
        try:
            resp = await client.get(url, headers = headers)
        except params.RETRYABLE_ERRORS:
            if attempt == attempts:
                raise
            await asyncio.sleep(domain.backoff_seconds(attempt, initial = wait_initial, maximum = wait_max))
            continue
        if pacer is not None:
            window = domain.rate_limit_window(resp.headers.get(params.RATE_LIMIT_HEADER))
            if window is not None and window[0] <= 0:
                pacer.hold(min(window[1], retry_after_cap))
        if resp.status_code not in params.TRANSIENT_STATUSES or attempt == attempts:
            return resp
        wait = domain.retry_after_seconds(resp.headers.get(params.RETRY_AFTER_HEADER), cap = retry_after_cap)
        if wait is None:
            wait = domain.backoff_seconds(attempt, initial = wait_initial, maximum = wait_max)
        if pacer is None:
            await asyncio.sleep(wait)
        elif not pacer.hold(wait):
            logger.info(f"[fetch] {url}: the crawl's waiting budget is spent, not retrying HTTP {resp.status_code}")
            return resp
    raise AssertionError("unreachable: the last attempt always returns or raises")
