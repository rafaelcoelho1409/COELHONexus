from __future__ import annotations


CACHE_PREFIX_SCORES      = "dd:rotator:bench:scores:"
CACHE_PREFIX_LEADERBOARD = "dd:rotator:bench:lb:"
CACHE_PREFIX_CANONICAL   = "dd:rotator:bench:canonical:"


def scores_key(canonical: str) -> str:
    return f"{CACHE_PREFIX_SCORES}{canonical}"


def leaderboard_key(source: str) -> str:
    return f"{CACHE_PREFIX_LEADERBOARD}{source}"


def canonical_key(provider_id: str) -> str:
    return f"{CACHE_PREFIX_CANONICAL}{provider_id}"
