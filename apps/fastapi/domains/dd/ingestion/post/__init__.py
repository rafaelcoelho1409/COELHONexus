"""Post-ingest normalization — monolith split + multi-page dedup."""
from __future__ import annotations

from .service import apply_to_store

__all__ = ["apply_to_store"]
