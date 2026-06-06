"""ycs/graph_builder — LLM batching + entity-resolution tunables.

Direct port of deprecated `services/youtube/graph_builder.py` defaults
(`L61, L150, L220-226, L245`)."""
from __future__ import annotations


# Concurrent LLM calls per batch — deprecated default. Tuned for free-
# tier NIM (40 RPM) with INTER_BATCH_SLEEP_S pacing between batches.
DEFAULT_BATCH_SIZE = 3

# Pacing between batches to stay under 40 RPM (deprecated `L150`).
# NOTE: deprecated used `time.sleep` inside an `async def`; preserved
# verbatim per the port-fidelity mandate.
INTER_BATCH_SLEEP_S = 2.0

# rapidfuzz token-ratio cutoff for the fuzzy-merge step. 75 was tuned
# empirically on the deprecated corpus — high enough to dodge false
# positives like "Cancun" vs "Canada", low enough to catch "St Kitts"
# vs "Saint Kitts".
FUZZ_MERGE_CUTOFF = 75

# Entity labels whose IDs are numeric or otherwise lexically-similar
# in ways that DON'T mean "same thing" — e.g. "$100,000" vs "$1,000,000"
# both have a high rapidfuzz ratio but are distinct values. Mirror of
# deprecated `SKIP_FUZZY_LABELS` (`L220-226`).
NUMERIC_LABELS_SKIP: frozenset[str] = frozenset({
    "Money",
    "Money amount",
    "Cost",
    "Number",
    "Date",
    "Currency",
})

# Schema-discovery sample size (deprecated `L282, L287`).
SCHEMA_DISCOVERY_SAMPLE_COUNT = 3
SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP = 10000
