"""ycs/ingestion — streaming ES → Qdrant pipeline.

Direct port of deprecated `services/youtube/ingestion.py`. Memory-safe
(one transcript in memory at a time), idempotent (deterministic point
ids), hybrid (dense NIM + sparse BM25)."""
from .keys import point_id
from .params import (
    QDRANT_COLLECTION,
    SCROLL_BATCH_SIZE,
)
from .service import (
    delete_points_for_videos,
    ensure_collection,
    fetch_metadata_from_es,
    fetch_transcripts_from_es,
    ingest_to_qdrant,
)


__all__ = [
    "QDRANT_COLLECTION",
    "SCROLL_BATCH_SIZE",
    "delete_points_for_videos",
    "ensure_collection",
    "fetch_metadata_from_es",
    "fetch_transcripts_from_es",
    "ingest_to_qdrant",
    "point_id",
]
