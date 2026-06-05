from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class CacheTTL:
    """Cache freshness for the three layers. `empty_payload` is the short TTL
    used when a fetcher returned `{}` — re-try sooner than a successful fetch."""
    scores:        int = 90 * 24 * 3600   # merged per-canonical scores
    leaderboard:   int =  7 * 24 * 3600   # full source leaderboard
    canonical:     int = 365 * 24 * 3600  # provider_id → canonical_name
    empty_payload: int =        3600      # short TTL on empty fetch


CACHE_TTL = CacheTTL()
