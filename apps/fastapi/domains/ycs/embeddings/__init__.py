"""ycs/embeddings — external-endpoint dense + FastEmbed BM25 sparse for
Qdrant hybrid.

2026-09-13: dense embeddings now route through the Settings-page-
configured endpoint (`domains/settings/embeddings`) instead of hardcoding NIM
directly — see `service.py`'s module docstring for why.

Public surface mirrors the old factory names so consumers don't need
churn beyond `NVIDIAEmbeddings` → `ExternalEmbeddings` and
`get_embedding_dimensions()` → `await get_embedding_info()`."""
from __future__ import annotations

from . import domain, errors, params, service
