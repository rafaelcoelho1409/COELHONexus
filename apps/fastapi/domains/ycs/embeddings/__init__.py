"""ycs/embeddings — external-endpoint dense + FastEmbed BM25 sparse for
Qdrant hybrid.

2026-09-13: dense embeddings now route through the Settings-page-
configured endpoint (`domains/llm/embeddings`) instead of hardcoding NIM
directly — see `service.py`'s module docstring for why.

Public surface mirrors the old factory names so consumers don't need
churn beyond `NVIDIAEmbeddings` → `ExternalEmbeddings` and
`get_embedding_dimensions()` → `await get_embedding_info()`."""
from .errors import (
    EmbeddingAPIError,
    EmbeddingEmptyQueryError,
    EmbeddingError,
)
from .service import (
    ExternalEmbeddings,
    create_dense_embeddings,
    create_sparse_embeddings,
    get_embedding_info,
)


__all__ = [
    "EmbeddingAPIError",
    "EmbeddingEmptyQueryError",
    "EmbeddingError",
    "ExternalEmbeddings",
    "create_dense_embeddings",
    "create_sparse_embeddings",
    "get_embedding_info",
]
