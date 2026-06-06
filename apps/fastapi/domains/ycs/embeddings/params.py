"""ycs/embeddings — NIM embedding-API config + retry/batch tunables.

Direct port of deprecated `services/youtube/embeddings.py:L36-56`. The
env var name `NVIDIA_API_KEY` is read here (not via the new BYOK
rotator) per deprecated convention — the YCS port spec is explicit
that this module does NOT route through the rotator."""
from __future__ import annotations

import os


# NIM embedding API endpoint + auth. Same key the rotator uses
# (`NVIDIA_API_KEY` env). Deprecated also read this env directly.
NIM_URL = "https://integrate.api.nvidia.com/v1"
NIM_KEY = os.environ.get("NVIDIA_API_KEY", "")

# Default + overridable model id. Override via Helm
# (`NVIDIA_EMBEDDING_MODEL` env) for A/B; remember to re-ingest the
# entire Qdrant collection on change (vectors aren't comparable across
# models).
EMBEDDING_MODEL = os.environ.get(
    "NVIDIA_EMBEDDING_MODEL",
    "nvidia/llama-nemotron-embed-1b-v2",
)

# Dimensions per model id. Used to size the Qdrant dense vector slot at
# collection-create time. Mirror of deprecated MODEL_DIMENSIONS map.
MODEL_DIMENSIONS: dict[str, int] = {
    "nvidia/llama-nemotron-embed-1b-v2":             2048,
    "nvidia/llama-3.2-nv-embedqa-1b-v2":             2048,
    "nvidia/llama-nemotron-embed-vl-1b-v2":          2048,
    "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1":2048,
    "nvidia/llama-3.2-nemoretriever-300m-embed-v1":  2048,
    "nvidia/nv-embedqa-e5-v5":                       1024,
    "nvidia/nv-embed-v1":                            4096,
    "nvidia/nv-embedcode-7b-v1":                     4096,
}

# Retry / batch / pacing — deprecated `embeddings.py:L98, L148, L156`.
MAX_RETRIES = 5
BATCH_SIZE = 50
BATCH_PAUSE_S = 2

# Outbound HTTP timeout — deprecated default (`L77`).
HTTP_TIMEOUT_S = 120.0


# Sparse model id — BM25 via `langchain_qdrant.FastEmbedSparse`. Pure
# CPU, ~zero overhead (tokenization + counting only).
SPARSE_MODEL_NAME = "Qdrant/bm25"
