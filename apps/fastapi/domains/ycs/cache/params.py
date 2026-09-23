"""ycs/cache — Redis cache prefix + default TTL."""
from __future__ import annotations


# 2026-09-23: renamed from "coelhonexus:rag:cache:" to fold in the
# `ycs` domain segment, matching the `coelhonexus:{domain}:...` standard
# adopted across dd/ycs/rr. Safe to rename freely — 1h TTL means stale
# entries under the old prefix just expire, no migration needed.
CACHE_PREFIX = "coelhonexus:ycs:rag:cache:"

# 1 hour — transcripts don't change often; cache-invalidate on /ingest
# (Wave 4 `tasks/youtube/qdrant.py:invalidate_cache`) covers the freshness
# case.
DEFAULT_TTL_S = 3600
