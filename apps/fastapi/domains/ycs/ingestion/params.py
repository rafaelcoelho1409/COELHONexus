"""ycs/ingestion — Qdrant collection name + ES scroll batch sizes.

Direct port of deprecated `services/youtube/ingestion.py` defaults
(`L44-46, L84, L139, L152-153`).

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

# Default chunker tunables for `ingest_to_qdrant` — fall back to the
# canonical chunker defaults if not overridden by caller.
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200

# Progress log throttle.
LOG_EVERY_N_TRANSCRIPTS = 50

# ES scroll context lifetime — 5 minutes is comfortable for the
# enumerate-then-process two-phase flow.
SCROLL_KEEPALIVE = "5m"
