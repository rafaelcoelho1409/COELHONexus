"""ycs/ingestion — streaming ES → Qdrant pipeline + per-video streaming
buffer.
Memory-safe
(one transcript in memory at a time), idempotent (deterministic point
ids), hybrid (dense NIM + sparse BM25)."""
from __future__ import annotations

from . import domain, keys, params, service
