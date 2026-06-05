"""digest_construct — schema + prompt cache-invalidation markers.

v3 (2026-05-29 PM, DD-SYNTH-SECTION-COUNT #3) — post-routing source-pool
MERGE. After per_section is built, sections whose PRIMARY source pools
overlap heavily (or whose primaries are a subset of another's) are
merged deterministically.
"""
from __future__ import annotations


DIGEST_SCHEMA_VERSION = "1.0"
DIGEST_PROMPT_VERSION = "v3-source-pool-merge-2026-05-29"
