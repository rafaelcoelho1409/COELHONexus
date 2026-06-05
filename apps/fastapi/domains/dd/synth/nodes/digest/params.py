"""digest_construct — Pydantic-enforced limits + source-pool merge thresholds."""
from __future__ import annotations


MAX_KEY_FACTS_PER_CONTRIB = 5
MIN_KEY_FACTS_PER_CONTRIB = 1
MAX_CONTRIBS_PER_SOURCE = 20
OVER_SPREAD_THRESHOLD = 2

# Source-pool merge (v3) — two sections are "the same scope" when their
# PRIMARY source sets are highly similar. Jaccard ≥ MERGE_JACCARD merges
# them; alternatively, if the smaller section's primaries are almost
# entirely contained in the larger's (containment ≥ MERGE_CONTAINMENT)
# the smaller brings no distinct authority and is folded in. Conservative
# by design — render-time dedup (#1) remains the safety net.
MERGE_JACCARD = 0.60
MERGE_CONTAINMENT = 0.80
# A section needs at least this many primary sources to "defend" itself
# as an independent H2; below it, containment-merge applies. Sections
# with zero primaries are always merge-eligible.
MERGE_MIN_PRIMARY_TO_DEFEND = 2

SUMMARY_MIN_CHARS = 20
SUMMARY_MAX_CHARS = 600
KEY_FACT_MIN_CHARS = 6
KEY_FACT_MAX_CHARS = 300
OVERALL_SUMMARY_MIN_CHARS = 30
OVERALL_SUMMARY_MAX_CHARS = 800
SOURCE_TITLE_MIN_CHARS = 3
SOURCE_TITLE_MAX_CHARS = 200

BLOB_PREFIX = "synth"
