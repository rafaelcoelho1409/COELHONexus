"""
Embedding Service — Dense + Sparse for Qdrant Hybrid Search

CONCEPT: Qdrant hybrid search needs TWO types of embeddings per document:
  1. Dense embeddings (semantic meaning) — from a transformer model
  2. Sparse embeddings (keyword matching) — BM25 via FastEmbed

Dense embeddings capture MEANING: "car" and "automobile" are close.
Sparse embeddings capture KEYWORDS: exact token matching like traditional search.
Combined, they give you the best of both worlds.

EMBEDDING MODEL CHOICE (2026 MTEB benchmarks):
- BAAI/bge-base-en-v1.5: 768 dims, good quality, CPU-friendly, battle-tested
- nomic-embed-text: 768 dims, 8K context, great lightweight option
- NV-Embed-v2: 4096 dims, highest quality, but requires GPU or API
- text-embedding-3-large: 3072 dims, OpenAI API, most deployed

We default to BAAI/bge-base-en-v1.5 for local CPU inference.
Switch to API-based models for production quality via the provider parameter.
"""
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import FastEmbedSparse


# =============================================================================
# Dense Embedding Models
# =============================================================================
EMBEDDING_MODELS = {
    "bge-base": {
        "model_name": "BAAI/bge-base-en-v1.5",
        "dimensions": 768,
    },
    "bge-small": {
        "model_name": "BAAI/bge-small-en-v1.5",
        "dimensions": 384,
    },
    "nomic": {
        "model_name": "nomic-ai/nomic-embed-text-v1.5",
        "dimensions": 768,
    },
}

DEFAULT_MODEL = "bge-base"


def create_dense_embeddings(model_key: str = DEFAULT_MODEL) -> HuggingFaceEmbeddings:
    """
    Create a dense embedding model for semantic search.

    CONCEPT: HuggingFaceEmbeddings loads the model locally.
    First call downloads the model (~130MB for bge-base).
    Subsequent calls use cached weights.
    The model runs on CPU — no GPU required.

    Usage:
        embeddings = create_dense_embeddings("bge-base")
        vectors = embeddings.embed_documents(["hello world"])  # list[list[float]]
        query_vec = embeddings.embed_query("hello")            # list[float]
    """
    config = EMBEDDING_MODELS[model_key]
    return HuggingFaceEmbeddings(
        model_name = config["model_name"],
        model_kwargs = {"device": "cpu"},
        encode_kwargs = {"normalize_embeddings": True},  # Normalize for cosine similarity
    )


def get_embedding_dimensions(model_key: str = DEFAULT_MODEL) -> int:
    """Return the vector dimensions for a given model."""
    return EMBEDDING_MODELS[model_key]["dimensions"]


# =============================================================================
# Sparse Embedding Model (BM25 for Qdrant hybrid)
# =============================================================================
def create_sparse_embeddings() -> FastEmbedSparse:
    """
    Create a BM25 sparse embedding model for keyword matching.

    CONCEPT: FastEmbedSparse uses the Qdrant/bm25 model to generate
    sparse vectors. These are like TF-IDF scores — they capture which
    specific words appear and how important they are.

    In Qdrant hybrid mode, sparse vectors handle keyword-exact queries
    ("React hooks tutorial") while dense vectors handle semantic queries
    ("how to manage state in frontend frameworks").
    """
    return FastEmbedSparse(model_name = "Qdrant/bm25")
