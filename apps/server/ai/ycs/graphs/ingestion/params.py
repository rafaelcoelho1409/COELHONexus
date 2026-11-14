"""Concurrency limits for the YCS ingestion graph."""
from __future__ import annotations

MAX_CONCURRENT_BATCHES = 2
MAX_CONCURRENT_NEO4J_BATCHES = 1

__all__ = ["MAX_CONCURRENT_BATCHES", "MAX_CONCURRENT_NEO4J_BATCHES"]
