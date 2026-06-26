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
