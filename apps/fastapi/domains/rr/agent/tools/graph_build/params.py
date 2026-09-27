"""Tunables for the RR agent's graph_build tool."""
from __future__ import annotations


# 2026-09-27: live-observed a single `EmbeddingError: APIConnectionError`
# permanently dropping a paper from Qdrant + Neo4j with no retry — the
# scan itself still reported `status: done, error: None`, so a top-ranked
# finding silently never made it into the knowledge base. Same
# transient-retry shape as `code_synth/params.py`'s `CALL_TIMEOUT_S`
# rationale, applied to the embed call in `_persist_one`.
EMBED_MAX_ATTEMPTS: int               = 3
EMBED_BACKOFF_S:    tuple[float, ...] = (2.0, 5.0)
