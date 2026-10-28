"""Shared fetching — pure helpers (what a server's headers say about waiting)."""
from __future__ import annotations
from . import params, patterns

import random
from urllib.parse import urlparse



def host_of(url: str) -> str:
    """The host of a URL, lower-cased ("" when it has none)."""
    return (urlparse(url).netloc or "").lower()


def retry_after_seconds(header: str | None, *, cap: float) -> float | None:
    """A `Retry-After` given in seconds (the HTTP-date form is ignored), bounded by what the caller is willing to wait."""
    if header is None:
        return None
    try:
        seconds = float(header.strip())
    except ValueError:
        return None
    return min(max(seconds, 0.0), cap)


def rate_limit_window(header: str | None) -> tuple[int, float] | None:
    """`(requests left, seconds until the window resets)` from a `RateLimit` header, or None when it says neither."""
    if not header:
        return None
    remaining, reset = patterns.REMAINING_RE.search(header), patterns.RESET_RE.search(header)
    if remaining is None or reset is None:
        return None
    return int(remaining.group(1)), float(reset.group(1))


def backoff_seconds(attempt: int, *, initial: float, maximum: float) -> float:
    """Jittered exponential backoff before the next try (`attempt` counts from 1, the try that just failed)."""
    base = min(maximum, initial * (params.BACKOFF_FACTOR ** (attempt - 1)))
    return base * random.uniform(*params.BACKOFF_JITTER)
