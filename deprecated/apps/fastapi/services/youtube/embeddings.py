"""
Embedding Service — NVIDIA NIM API + BM25 Sparse for Qdrant Hybrid Search

CONCEPT: Qdrant hybrid search needs TWO types of embeddings per document:
  1. Dense embeddings (semantic meaning) — NVIDIA NIM embedding API
  2. Sparse embeddings (keyword matching) — BM25 via FastEmbed (local, lightweight)

Dense embeddings use NVIDIA NIM API:
  - Zero CPU usage (server-side GPU inference)
  - Same API key as LLM models (already configured)
  - Single model with exponential backoff retry on 429
  - Rate-limit-aware pacing for bulk ingestion (30 req/min)
  - Configurable via NVIDIA_EMBEDDING_MODEL env var

IMPORTANT: All vectors in a Qdrant collection MUST come from the same model.
Different models produce incompatible vector spaces. Do NOT switch models
without re-ingesting the entire collection.

Available models (tested 2026-04-11, see docs/NVIDIA-NIM-EMBEDDING-MODELS.md):
  2048d: nvidia/llama-nemotron-embed-1b-v2 (default, best quality)
  2048d: nvidia/llama-3.2-nv-embedqa-1b-v2 (multilingual)
  1024d: nvidia/nv-embedqa-e5-v5 (mature, shorter context)
  4096d: nvidia/nv-embed-v1 (highest quality, older)
"""
import os
import time
import logging
from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration — change model via env var or Helm values
# =============================================================================
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY", "")

# Default: best quality model with 8K context, 2048 dimensions
# Override: set NVIDIA_EMBEDDING_MODEL env var in Helm values or .env
NVIDIA_EMBEDDING_MODEL = os.environ.get(
    "NVIDIA_EMBEDDING_MODEL",
    "nvidia/llama-nemotron-embed-1b-v2",
)

# Dimensions per model (must match Qdrant collection)
MODEL_DIMENSIONS = {
    "nvidia/llama-nemotron-embed-1b-v2": 2048,
    "nvidia/llama-3.2-nv-embedqa-1b-v2": 2048,
    "nvidia/llama-nemotron-embed-vl-1b-v2": 2048,
    "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1": 2048,
    "nvidia/llama-3.2-nemoretriever-300m-embed-v1": 2048,
    "nvidia/nv-embedqa-e5-v5": 1024,
    "nvidia/nv-embed-v1": 4096,
    "nvidia/nv-embedcode-7b-v1": 4096,
}


class NVIDIAEmbeddings(Embeddings):
    """
    NVIDIA NIM Embedding API with retry and rate-limit-aware pacing.

    Single model — all vectors in the same geometric space.
    On 429: exponential backoff retry (2s, 4s, 8s, 16s, max 5 retries).
    For bulk ingestion: self-paces at ~30 req/min to avoid hitting limits.

    Implements LangChain Embeddings interface:
      - embed_documents(texts) → list[list[float]]
      - embed_query(text) → list[float]
    """
    def __init__(
        self, 
        model: str = NVIDIA_EMBEDDING_MODEL):
        self.model = model
        self.dimensions = MODEL_DIMENSIONS.get(model, 2048)
        import httpx
        self._client = httpx.Client(timeout = 120.0)
        logger.info(f"[embeddings] Using {model} ({self.dimensions}d) via NVIDIA NIM API")

    def _call_api(
        self,
        texts: list[str],
        input_type: str = "passage") -> list[list[float]]:
        """
        Call NVIDIA NIM embedding API.

        Retry policy:
          - Empty input → return [] immediately (NVIDIA API rejects this as 400; pointless to call)
          - HTTP 429 or 5xx → exponential backoff (2s, 4s, 8s, 16s, 32s; max 5 retries)
          - HTTP 4xx (other) → raise immediately (deterministic client error, retry is useless and blocks the event loop)
          - Network / transient exception → retry with same backoff
        """
        # Guard: NVIDIA NIM rejects empty lists AND lists with empty/whitespace elements with
        # "Input list must be non-empty and all elements must be non-empty." A retry loop on
        # this deterministic 400 blocks the event loop ~62s and trips the k8s liveness probe.
        if not texts or all(not t or not t.strip() for t in texts):
            return []
        max_retries = 5
        for attempt in range(max_retries + 1):
            try:
                response = self._client.post(
                    f"{NVIDIA_URL}/embeddings",
                    headers = {
                        "Authorization": f"Bearer {NVIDIA_KEY}",
                        "Content-Type": "application/json",
                    },
                    json = {
                        "model": self.model,
                        "input": texts,
                        "input_type": input_type,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return [item["embedding"] for item in data["data"]]
                # Transient: 429 rate-limit or any 5xx server error → retry
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < max_retries:
                        wait = 2 ** (attempt + 1)
                        logger.info(f"[embeddings] HTTP {response.status_code}, retry {attempt + 1}/{max_retries} in {wait}s")
                        time.sleep(wait)
                        continue
                    raise RuntimeError(f"Embedding API HTTP {response.status_code} after {max_retries} retries")
                # 4xx (other) — deterministic client error, do NOT retry
                raise RuntimeError(f"Embedding API returned {response.status_code}: {response.text[:200]}")
            except RuntimeError:
                # Re-raise our own classified errors without wrapping in retry
                raise
            except Exception as e:
                # Network / transient exception → retry
                if attempt < max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"[embeddings] Network error: {e}, retry {attempt + 1}/{max_retries} in {wait}s")
                    time.sleep(wait)
                    continue
                raise

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        Embed documents with rate-limit-aware batching.

        Batches of 50 texts, with 2s pause between batches.
        50 texts × 30 batches/min = ~1500 texts/min (well within 40 RPM).
        For 1800 chunks: ~36 batches × 2s pause = ~72s pauses + API time ≈ ~3-5 min total.
        """
        if not texts:
            return []
        BATCH_SIZE = 50
        all_embeddings = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            embeddings = self._call_api(batch, input_type = "passage")
            all_embeddings.extend(embeddings)
            # Rate-limit pacing: pause between batches to stay under 40 RPM
            if i + BATCH_SIZE < len(texts):
                time.sleep(2)
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query — no pacing needed (single request)."""
        if not text or not text.strip():
            raise ValueError("Cannot embed an empty query")
        result = self._call_api([text], input_type = "query")
        if not result:
            raise ValueError("Embedding API returned no result for query")
        return result[0]


# =============================================================================
# Factory Functions
# =============================================================================
def create_dense_embeddings() -> NVIDIAEmbeddings:
    """
    Create NVIDIA NIM embedding client.
    Model configurable via NVIDIA_EMBEDDING_MODEL env var.
    Default: nvidia/llama-nemotron-embed-1b-v2 (2048d, 8K context).
    """
    return NVIDIAEmbeddings(model = NVIDIA_EMBEDDING_MODEL)


def get_embedding_dimensions() -> int:
    """Return vector dimensions for the configured model."""
    return MODEL_DIMENSIONS.get(NVIDIA_EMBEDDING_MODEL, 2048)


# =============================================================================
# Sparse Embedding Model (BM25 — local, minimal CPU)
# =============================================================================
def create_sparse_embeddings() -> FastEmbedSparse:
    """
    BM25 sparse embeddings for keyword matching.
    Stays local — just tokenization + counting (no neural network, no CPU impact).
    """
    return FastEmbedSparse(model_name = "Qdrant/bm25")
