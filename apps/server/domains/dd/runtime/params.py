"""Tunables + shared Redis scripts for the DD runtime (LLM-usage counters,
lock release)."""
from __future__ import annotations


COUNTER_TTL_S = 24 * 60 * 60

# Compare-and-delete — release only if the lock still holds OUR thread_id.
# Stops a slow finally from clearing a lock a racing later start has taken.
CAD_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)
