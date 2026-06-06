"""ycs/cache — Redis cache prefix + default TTL.

Constants kept verbatim from deprecated `services/youtube/cache.py:L23-24`
so re-using an existing Redis is a no-op."""
from __future__ import annotations


# Exact deprecated string — DO NOT change without a Redis migration plan
# (existing cache entries would orphan under the old prefix).
CACHE_PREFIX = "coelhonexus:rag:cache:"

# 1 hour — transcripts don't change often; cache-invalidate on /ingest
# (Wave 4 `tasks/youtube/qdrant.py:invalidate_cache`) covers the freshness
# case.
DEFAULT_TTL_S = 3600
