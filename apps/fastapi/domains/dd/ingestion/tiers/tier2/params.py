"""Tier 2 — tunable scalars."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier2/1.0"
TIMEOUT_S = 30.0
CONCURRENCY = 8
MIN_OK_BYTES = 200
# Tier-2-gated stub floor: pages below this never reach the manifest (nav
# fragments / course scaffolding). Tier 1 keeps its own 300B post-split floor
# (post.params.SPLIT_MIN_SECTION_BYTES) — this constant must NOT be reused
# there.
STUB_MIN_BYTES = 1024
