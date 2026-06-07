"""ycs/pipeline_task — chain dispatch tunables.

Single source of truth for the LLM-entity-extraction batch size used
when chaining `ingest_to_neo4j` from the Videos pipeline. Mirror of
the deprecated default (`tasks/youtube/neo4j.py::ingest_to_neo4j`
default `batch_size=3`)."""
from __future__ import annotations


NEO4J_BATCH_SIZE: int = 3

# How long the pipeline's dispatch params (video_ids + flags) live in
# Redis. Used by the FastHTML Ingest page's "Rerun" button to re-fire
# the chain without making the user re-pick videos from Search.
# 24h is long enough for a user to come back to a failed run the next
# day, short enough that stale keys don't pile up.
PIPELINE_STATE_TTL_S: int = 86400

# Redis key namespace for the dispatch-params lookup. Pairs with
# `keys.pipeline_state_key(extract_id)`.
PIPELINE_STATE_PREFIX: str = "ycs:pipeline:"
