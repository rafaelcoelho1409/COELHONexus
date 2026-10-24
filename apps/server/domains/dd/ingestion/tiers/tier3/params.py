"""Tier 3 — tunable scalars."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier3/1.0"
TIMEOUT_S = 30.0
CONCURRENCY = 8
MIN_OK_BYTES = 200

# Nested sitemap depth limit (sitemap index → sitemap → …). Three levels is
# the deepest anyone in the wild has been observed using.
INDEX_MAX_DEPTH = 3
