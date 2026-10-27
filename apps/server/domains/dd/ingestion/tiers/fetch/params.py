"""Shared fetching — tunable scalars."""
from __future__ import annotations


# Worth asking again: too many requests, and the server's own hiccups.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# A crawl of many pages from one host. A site that rate-limits (Hugging Face: 100 docs pages per 5 minutes per IP) says how
# long to wait; the crawl waits that out — all its requests together — instead of dropping every page past the limit.
CRAWL_ATTEMPTS = 6
CRAWL_WAIT_INITIAL_S = 1.0
CRAWL_WAIT_MAX_S = 15.0
# The longest single wait a site may ask for (a 5-minute window plus a little); a longer ask is not waited for.
CRAWL_RETRY_AFTER_MAX_S = 330.0
# Holds that end within this many seconds of one already announced are the same pause (concurrent requests each ask for
# it a moment apart).
ANNOUNCE_MERGE_S = 2.0
# All the waiting one crawl does, added up. Inside a task's one-hour limit with room left for post-processing, and enough
# for Transformers' 771 pages at 100 per window (about 35 minutes of waiting).
CRAWL_WAIT_BUDGET_S = 2700.0
