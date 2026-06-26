"""ycs/embeddings — NIM embedding-API config + retry/batch tunables.

Provider key is resolved at *call* time via `domains.llm.credentials.resolve_key()`
rather than at module load — the BYOK rotator migration moved keys into the
MinIO-backed store; reading `NVIDIA_API_KEY` at import caused `Illegal header
value b'Bearer '` 5xx errors during Qdrant ingest."""
from __future__ import annotations

import os


# NIM embedding API endpoint. Same provider the rotator uses.
NIM_URL = "https://integrate.api.nvidia.com/v1"
# Sentinel for the credential resolver — the actual key value gets
# resolved per-call inside `service.NVIDIAEmbeddings._call_api`. Reading
# `os.environ[NIM_KEY_ENV]` at module load was the original bug.
NIM_KEY_ENV = "NVIDIA_API_KEY"

# (`NVIDIA_EMBEDDING_MODEL` env) for A/B; remember to re-ingest the
# entire Qdrant collection on change (vectors aren't comparable across
# models).
EMBEDDING_MODEL = os.environ.get(
    "NVIDIA_EMBEDDING_MODEL",
    "nvidia/llama-nemotron-embed-1b-v2",
)

# Dimensions per model id. Used to size the Qdrant dense vector slot at
# `baai/bge-m3` added for the graph_builder semantic
# entity-resolution check (Option 2 fix for false fuzzy merges) — the
# Qdrant path keeps using `llama-nemotron-embed-1b-v2`. BGE-M3 was
# picked empirically: it's multilingual (100+ langs, key for the
# Brazilian Portuguese entities) and gives a clean cosine gap between
# true and false merges at threshold 0.85 on short entity-ID strings.
MODEL_DIMENSIONS: dict[str, int] = {
    "nvidia/llama-nemotron-embed-1b-v2":             2048,
    "nvidia/llama-3.2-nv-embedqa-1b-v2":             2048,
    "nvidia/llama-nemotron-embed-vl-1b-v2":          2048,
    "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1":2048,
    "nvidia/llama-3.2-nemoretriever-300m-embed-v1":  2048,
    "nvidia/nv-embedqa-e5-v5":                       1024,
    "nvidia/nv-embed-v1":                            4096,
    "nvidia/nv-embedcode-7b-v1":                     4096,
    "baai/bge-m3":                                   1024,
}

MAX_RETRIES = 5
BATCH_SIZE = 50
BATCH_PAUSE_S = 2

# Outbound HTTP timeout — deprecated default (`L77`).
HTTP_TIMEOUT_S = 120.0


# Sparse model id — BM25 via `langchain_qdrant.FastEmbedSparse`. Pure
# CPU, ~zero overhead (tokenization + counting only).
SPARSE_MODEL_NAME = "Qdrant/bm25"
