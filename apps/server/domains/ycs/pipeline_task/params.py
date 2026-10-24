"""ycs/pipeline_task — dispatch + streaming-coordination tunables.

2026-09-13: `NEO4J_BATCH_SIZE` removed — the Videos-tab pipeline no
longer dispatches one `ingest_to_neo4j` call for the whole batch (with
this constant controlling its internal granularity). Per-video
streaming fan-out (`extract/task.py`'s `_on_video_indexed`) now calls
`ingest_to_neo4j.si([video_id], 1, ...)` directly, one Celery task per
video — the "batch of N, batch_size=1 for per-video progress" tradeoff
this constant used to encode no longer applies; every dispatch already
IS one video."""
from __future__ import annotations


# How long the pipeline's dispatch params (video_ids + flags) live in
# Redis. Used by the FastHTML Ingest page's "Rerun" button to re-fire
# the chain without making the user re-pick videos from Search.
# 24h is long enough for a user to come back to a failed run the next
# day, short enough that stale keys don't pile up.
PIPELINE_STATE_TTL_S: int = 86400

# Redis key namespace for the dispatch-params lookup. Pairs with
# `keys.pipeline_state_key(extract_id)`.
PIPELINE_STATE_PREFIX: str = "coelhonexus:ycs:pipeline:"

# 2026-09-14: cooperative-cancel flag TTL (`keys.pipeline_cancel_key`).
# Same window as PIPELINE_STATE_TTL_S — no reason for the cancel flag to
# outlive (or expire before) the run's own dispatch-state bookkeeping.
PIPELINE_CANCEL_TTL_S: int = PIPELINE_STATE_TTL_S
