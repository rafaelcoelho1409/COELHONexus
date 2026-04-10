"""
Embedding Service — Dense + Sparse for Qdrant Hybrid Search

CONCEPT: Qdrant hybrid search needs TWO types of embeddings per document:
  1. Dense embeddings (semantic meaning) — from a transformer model
  2. Sparse embeddings (keyword matching) — BM25 via FastEmbed

Both use FastEmbed (ONNX Runtime) — NO PyTorch.
This saves ~600-800MB of RAM vs sentence-transformers/PyTorch,
letting embeddings coexist with Playwright browser contexts in 8Gi.

EMBEDDING MODEL CHOICE:
- bge-small: 384d, 67MB quantized ONNX — lightweight, fast
- bge-base: 768d, 210MB quantized ONNX — higher quality
- nomic: 768d — good multilingual

We default to bge-small for minimal memory. The quality gap vs bge-base
is compensated by BM25 sparse hybrid search + FlashRank reranking.
"""
import os
# Disable ONNX Runtime memory arena pre-allocation.
# Without this, ONNX allocates large memory blocks upfront (~2-5GB)
# that spike RSS past the K8s memory limit and trigger OOMKill.
# With this, ONNX allocates memory on-demand — uses less peak RAM.
os.environ.setdefault("ORT_DISABLE_MEMORY_ARENA", "1")

from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_qdrant import FastEmbedSparse


# =============================================================================
# Dense Embedding Models (all via FastEmbed ONNX — no PyTorch)
# =============================================================================
EMBEDDING_MODELS = {
    "bge-small": {
        "model_name": "BAAI/bge-small-en-v1.5",
        "dimensions": 384,
    },
    "bge-base": {
        "model_name": "BAAI/bge-base-en-v1.5",
        "dimensions": 768,
    },
    "nomic": {
        "model_name": "nomic-ai/nomic-embed-text-v1.5",
        "dimensions": 768,
    },
}

DEFAULT_MODEL = "bge-base"


def create_dense_embeddings(model_key: str = DEFAULT_MODEL) -> FastEmbedEmbeddings:
    """
    Create a dense embedding model for semantic search.

    CONCEPT: FastEmbedEmbeddings uses ONNX Runtime instead of PyTorch.
    Models are quantized (INT8) and optimized for CPU inference.
    bge-small: ~67MB model, ~100MB total RSS (vs ~580MB with PyTorch).

    The LangChain Embeddings interface is identical:
        embeddings.embed_documents(["hello world"])  -> list[list[float]]
        embeddings.embed_query("hello")              -> list[float]
    """
    config = EMBEDDING_MODELS[model_key]
    return FastEmbedEmbeddings(model_name = config["model_name"])


def get_embedding_dimensions(model_key: str = DEFAULT_MODEL) -> int:
    """Return the vector dimensions for a given model."""
    return EMBEDDING_MODELS[model_key]["dimensions"]


# =============================================================================
# Sparse Embedding Model (BM25 for Qdrant hybrid)
# =============================================================================
def create_sparse_embeddings() -> FastEmbedSparse:
    """
    Create a BM25 sparse embedding model for keyword matching.
    Both dense and sparse now use the same ONNX Runtime backend.
    """
    return FastEmbedSparse(model_name = "Qdrant/bm25")
