"""Pure helpers for YCS ingestion pipeline progress data."""
from __future__ import annotations

from typing import Any


def partition_totals(transcript_docs: list[dict[str, Any]]) -> dict[str, int]:
    """Build the expected partition count for each transcript video."""
    return {
        str(document["video_id"]): int(document["part_total"])
        for document in transcript_docs
        if document.get("video_id") and document.get("part_total")
    }


__all__ = ["partition_totals"]
