"""Dispatch — tunable scalars + tier-source priority order."""
from __future__ import annotations


# Best-source order in catalog entries (highest priority first).
KIND_PRIORITY: tuple[str, ...] = (
    "llms_full",
    "llms_txt",
    "sitemap",
    "docs",
    "github",
)

REDIS_CONNECT_TIMEOUT_S = 3.0
REDIS_OP_TIMEOUT_S      = 10.0

# Watcher poll interval — cooperative `raise_if_cancelled()` doesn't fire
# during Crawl4AI `arun_many` (30-60s blocking await).
CANCEL_POLL_S = 1.0

# Settle gap between cleanup passes so in-flight parallel writes can finish
# (tier 2/3/4a stream writes in coroutines that may unwind after cancel).
CLEANUP_SETTLE_S = 0.8
