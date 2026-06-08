"""ycs/pipeline_task — chain dispatch tunables.

Single source of truth for the LLM-entity-extraction batch size used
when chaining `ingest_to_neo4j` from the Videos pipeline.

2026-06-08 — lowered 3 → 1 for the Videos-tab pipeline so the
Ingest-page progress bar advances per video (not per batch). Trade-
off: ~1.5–2× slower for high-N runs because LLM calls go sequential
instead of `aconvert_to_graph_documents([d1,d2,d3])`-style parallel.
Per [[feedback_kd_quality_over_speed]] this UX-clarity tradeoff is
acceptable. The deprecated path used 3 because it had no UI to be
granular about; channel-pipeline (full_channel_pipeline) keeps the
larger default via `graph_builder.params.DEFAULT_BATCH_SIZE`."""
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
