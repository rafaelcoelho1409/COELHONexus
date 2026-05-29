"""digest_construct constants — versioning, tunables, compiled regexes."""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
DIGEST_SCHEMA_VERSION = "1.0"
# v2 (2026-05-29, DD-SYNTH-SECTION-RECYCLING #5) — routing prompt pushes
# single-best-section homing + over-spread threshold tightened 3 → 2 to
# curb the same source feeding multiple overlapping sections (the upstream
# driver of hollow cross-reference sections).
# v3 (2026-05-29 PM, DD-SYNTH-SECTION-COUNT #3) — post-routing source-pool
# MERGE. After per_section is built, sections whose PRIMARY source pools
# overlap heavily (or whose primaries are a subset of another's) are merged
# deterministically — the definitive overlap signal the heading/embedding
# proxy could not see (e.g. ch-13's four differently-titled "OpenTelemetry
# config" sections all draw the same 2-3 OTEL docs). Merged sections are
# dropped by sawc so they never render as hollow cross-references. Bumped
# to force a cold digest re-run with the merge active.
DIGEST_PROMPT_VERSION = "v3-source-pool-merge-2026-05-29"

_MAX_KEY_FACTS_PER_CONTRIB = 5
_MIN_KEY_FACTS_PER_CONTRIB = 1
_MAX_CONTRIBS_PER_SOURCE = 20
_OVER_SPREAD_THRESHOLD = 2

# Source-pool merge (v3) — two sections are "the same scope" when their
# PRIMARY source sets are highly similar. Jaccard ≥ _MERGE_JACCARD merges
# them; alternatively, if the smaller section's primaries are almost
# entirely contained in the larger's (containment ≥ _MERGE_CONTAINMENT)
# the smaller brings no distinct authority and is folded in. Conservative
# by design — we would rather leave a mild overlap than merge two genuinely
# distinct sections, so render-time dedup (#1) remains the safety net.
_MERGE_JACCARD = 0.60
_MERGE_CONTAINMENT = 0.80
# A section needs at least this many primary sources to "defend" itself as
# an independent H2; below it, containment-merge applies (mirrors the
# outline validator's "defensible by >=N source docs" rule). Sections with
# zero primaries are always merge-eligible.
_MERGE_MIN_PRIMARY_TO_DEFEND = 2
_SUMMARY_MIN_CHARS = 20
_SUMMARY_MAX_CHARS = 600
_KEY_FACT_MIN_CHARS = 6
_KEY_FACT_MAX_CHARS = 300
_OVERALL_SUMMARY_MIN_CHARS = 30
_OVERALL_SUMMARY_MAX_CHARS = 800
_SOURCE_TITLE_MIN_CHARS = 3
_SOURCE_TITLE_MAX_CHARS = 200

# 12-hex hash format (matches vault.py sentinels)
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

_VAULT_HASH_IN_TEXT_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"\s*/>')
