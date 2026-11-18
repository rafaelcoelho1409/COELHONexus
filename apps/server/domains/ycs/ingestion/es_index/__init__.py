"""ycs/ingestion/es_index — async bulk-index helpers for metadata, transcripts, and passages.
Used by Wave 4 Celery tasks (`extract/task.py`) after yt-dlp + Playwright
extraction. Canonical metadata/transcript documents and transcript passage
projections target their stable aliases by default."""
from __future__ import annotations
from . import domain, keys, params, service

__all__ = ["domain", "keys", "params", "service"]
