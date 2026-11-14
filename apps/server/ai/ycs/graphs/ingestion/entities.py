"""Runtime dependencies for the standalone YCS ingestion graph."""
from __future__ import annotations

import asyncio

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen = True, slots = True)
class IngestionContext:
    es_client: Any
    extractor: Any
    transcript_service: Any | None
    run_id: str | None
    include_transcription: bool
    languages: list[str] | None
    total_videos: int
    reporter: Any
    qdrant_semaphore: asyncio.Semaphore
    neo4j_semaphore: asyncio.Semaphore
    cancel_check: Callable[[], Any] | None = None


__all__ = ["IngestionContext"]
