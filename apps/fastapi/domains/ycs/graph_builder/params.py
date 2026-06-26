from __future__ import annotations

import os as _os


DEFAULT_BATCH_SIZE = 3

# Overridable via YCS_NEO4J_CONCURRENCY; semaphore keeps this many transcripts in flight.
EXTRACT_CONCURRENCY = max(
    1, int(_os.environ.get("YCS_NEO4J_CONCURRENCY", "3") or "3"),
)

# Must exceed YCS_NEO4J_EXTRACT_TIMEOUT_S (default 300s) or the watchdog fires before the call's own deadline.
GRAPH_BATCH_TIMEOUT_S = max(
    300.0, float(_os.environ.get("YCS_NEO4J_BATCH_WATCHDOG_S", "600") or "600"),
)

# 3 consecutive 0-entity results on a working corpus signals a dead arm, not empty videos.
MAX_CONSECUTIVE_NONPRODUCTIVE = 3

# fuzz.ratio pre-filter; BGE-M3 cosine gate at 0.85 catches false positives like Astronomia↔Gastronomia.
FUZZ_MERGE_CUTOFF = 75

RESOLVE_EMBED_MODEL = "baai/bge-m3"
EMBED_COSINE_CUTOFF = 0.85

# Numeric/date labels: high fuzz ratio ≠ same entity (e.g. "$100k" vs "$1M").
NUMERIC_LABELS_SKIP: frozenset[str] = frozenset({
    "Money",
    "Money amount",
    "Cost",
    "Number",
    "Date",
    "Currency",
})

SCHEMA_DISCOVERY_SAMPLE_COUNT = 3
SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP = 10000
