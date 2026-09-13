from __future__ import annotations

import os as _os


DEFAULT_BATCH_SIZE = 3

# Overridable via YCS_NEO4J_CONCURRENCY; semaphore keeps this many transcripts in flight.
# 2026-09-13: 5 -> 3 -> 5 same day. First bump to 5 looked like it caused a
# regression (only 10/24 videos ever attempted on a Raiam Santos run,
# circuit breaker tripping almost every segment). Reverted to 3 as the
# suspected cause — but the SAME failure (every first-batch call timing
# out simultaneously at exactly 120s, 0 successes) reproduced identically
# at concurrency=3, proving concurrency was never the real driver. Actual
# cause: chain/service.py's build_ycs_neo4j_pinned_chain() had an explicit
# 120.0s timeout that was too tight for this call shape (large transcripts
# → large completions) — fixed there (raised to 400.0s). Back to 5 now
# that the real cause is fixed. The circuit-breaker abandonment bug
# (graph_builder/service.py's `asyncio.as_completed` + `break` drops
# whatever's still in-flight in that segment untried) is still real and
# pre-existing, independent of this constant — worth a dedicated fix
# separately. Do not push toward doc_distill's CONCURRENCY=10 — these
# calls carry much larger prompts/completions than doc_distill's 600-
# token summaries and are more likely to hit free-tier rate limits sooner.
EXTRACT_CONCURRENCY = max(
    1, int(_os.environ.get("YCS_NEO4J_CONCURRENCY", "5") or "5"),
)

# Must exceed YCS_NEO4J_EXTRACT_TIMEOUT_S (default 300s) or the watchdog fires before the call's own deadline.
GRAPH_BATCH_TIMEOUT_S = max(
    300.0, float(_os.environ.get("YCS_NEO4J_BATCH_WATCHDOG_S", "600") or "600"),
)

# 3 consecutive 0-entity results on a working corpus signals a dead arm, not empty videos.
MAX_CONSECUTIVE_NONPRODUCTIVE = 3

# fuzz.ratio pre-filter; embedding cosine gate at 0.85 catches false positives like Astronomia↔Gastronomia.
FUZZ_MERGE_CUTOFF = 75

# 2026-09-13: tuned against baai/bge-m3's score distribution back when
# entity-resolution pinned that model specifically via its own
# NVIDIAEmbeddings instance. Now shares the main embedding-endpoint
# singleton (no per-call model pinning) — may need re-tuning against
# whatever the endpoint currently resolves to.
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
