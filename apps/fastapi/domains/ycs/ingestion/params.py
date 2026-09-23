"""ycs/ingestion — Qdrant collection name + ES scroll batch sizes.
ES index names live in `infra/elasticsearch/params.py` — `service.py`
imports them from there rather than duplicating the constants."""
from __future__ import annotations


# Qdrant collection name — kept verbatim so re-using existing data is
# a no-op.
QDRANT_COLLECTION = "youtube-transcripts"

# ES scroll batch size for the transcript iterator.
SCROLL_BATCH_SIZE = 50

# Non-streaming fetch batch size — used by `fetch_transcripts_from_es`
# (graph_builder reads transcripts that way).
FETCH_BATCH_SIZE = 100

# canonical chunker defaults if not overridden by caller.
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200

# Progress log throttle.
LOG_EVERY_N_TRANSCRIPTS = 50

# Cross-video chunk packing . NIM embedding latency is
# per-CALL dominated (~11-15 s per call whether it carries 5 texts or
# 50 — measured 60.7 s per-video vs 11.2 s packed for the same 48
# chunks). Chunks are therefore accumulated ACROSS videos and flushed
# (embed dense+sparse → upsert) once the buffer reaches this size.
# 50 matches `embeddings.params.BATCH_SIZE` so each flush is exactly
# one NIM call; progress advances per flush as videos complete.
FLUSH_CHUNKS = 50

# ES scroll context lifetime — 5 minutes is comfortable for the
# enumerate-then-process two-phase flow.
SCROLL_KEEPALIVE = "5m"

# 2026-09-13: streaming-buffer Redis namespace (`keys.py`'s
# `qdrant_buffer_key`/`qdrant_flush_lock_key`). Must match
# `pipeline_task.params.PIPELINE_STATE_PREFIX`'s literal value — kept
# as a separate constant (not imported) to avoid pulling
# `pipeline_task/__init__.py`'s full Celery-task import chain into
# `ingestion` just to read one string. Same TTL as that module's
# `PIPELINE_STATE_TTL_S` for the same reason (24h — long enough to
# survive a slow run, short enough that a crashed run's buffer doesn't
# linger in Redis forever).
STREAMING_KEY_PREFIX = "coelhonexus:ycs:pipeline:"
STREAMING_KEY_TTL_S = 86400
