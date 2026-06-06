"""ycs/embeddings — NIM dense + FastEmbed BM25 sparse for Qdrant hybrid.

Direct port of deprecated `services/youtube/embeddings.py`. Does NOT
route through the LLM rotator (`docs/YCS-PORT-PLAN-2026-06-06.md`
Wave 3.1 — deprecated didn't, so neither do we).

Public surface mirrors deprecated factory names so consumers can
import without adaptation."""
from .errors import (
    EmbeddingAPIError,
    EmbeddingEmptyQueryError,
    EmbeddingError,
)
from .params import EMBEDDING_MODEL, MODEL_DIMENSIONS
from .service import (
    NVIDIAEmbeddings,
    create_dense_embeddings,
    create_sparse_embeddings,
    get_embedding_dimensions,
)


__all__ = [
    "EMBEDDING_MODEL",
    "EmbeddingAPIError",
    "EmbeddingEmptyQueryError",
    "EmbeddingError",
    "MODEL_DIMENSIONS",
    "NVIDIAEmbeddings",
    "create_dense_embeddings",
    "create_sparse_embeddings",
    "get_embedding_dimensions",
]
