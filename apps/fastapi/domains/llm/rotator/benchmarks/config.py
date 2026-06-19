from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class CacheTTL:
    """`empty_payload` retries sooner than a successful fetch."""
    scores:        int = 90 * 24 * 3600
    leaderboard:   int =  7 * 24 * 3600
    canonical:     int = 365 * 24 * 3600
    empty_payload: int =        3600


CACHE_TTL = CacheTTL()
