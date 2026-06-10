"""ycs/pipeline_task — chain dispatch tunables.

Single source of truth for the LLM-entity-extraction batch size used
when chaining `ingest_to_neo4j` from the Videos pipeline.

2026-06-08 — lowered 3 → 1 for the Videos-tab pipeline so the
Ingest-page progress bar advances per video (not per batch).

2026-06-10 — the sequential-for-granularity tradeoff is GONE:
`extract_and_store_graph` now runs a streaming pool that completes
(and reports) one video at a time at ANY width. `batch_size <= 1`
means "use `graph_builder.params.EXTRACT_CONCURRENCY`" (default 3,
env `YCS_NEO4J_CONCURRENCY`); values > 1 set the pool width
explicitly. Keeping 1 here = default concurrency + per-video bar."""
from __future__ import annotations


NEO4J_BATCH_SIZE: int = 1

# How long the pipeline's dispatch params (video_ids + flags) live in
# Redis. Used by the FastHTML Ingest page's "Rerun" button to re-fire
# the chain without making the user re-pick videos from Search.
# 24h is long enough for a user to come back to a failed run the next
# day, short enough that stale keys don't pile up.
PIPELINE_STATE_TTL_S: int = 86400

# Redis key namespace for the dispatch-params lookup. Pairs with
# `keys.pipeline_state_key(extract_id)`.
PIPELINE_STATE_PREFIX: str = "ycs:pipeline:"
