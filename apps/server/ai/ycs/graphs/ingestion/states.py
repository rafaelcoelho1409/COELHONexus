"""Transient state for one bounded YCS ingestion work batch."""
from __future__ import annotations

from typing import Any, TypedDict


class IngestionChunkState(TypedDict, total = False):
    video_ids: list[str]
    extract_id: str | None
    metadata_videos: list[dict[str, Any]]
    metadata_result: dict[str, Any]
    transcription_result: dict[str, Any]
    transcript_docs: list[dict[str, Any]]
    cached_ids: list[str]
    no_transcript_ids: list[str]
    fetch_failed_ids: list[str]
    transcription_stats: dict[str, Any]
    dispatched_ids: list[str]
    elasticsearch_result: dict[str, Any]
    index_failed_ids: list[str]
    qdrant_result: dict[str, Any]
    neo4j_result: dict[str, Any]


__all__ = ["IngestionChunkState"]
