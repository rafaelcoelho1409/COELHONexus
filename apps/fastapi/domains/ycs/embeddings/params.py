"""ycs/embeddings — batch/pacing tunables.

2026-09-13: NIM-specific constants (NIM_URL, NIM_KEY_ENV, EMBEDDING_MODEL,
MODEL_DIMENSIONS) removed — embeddings now call the Settings-page-
configured endpoint (`domains/settings/embeddings`), which owns its own
connection details and resolves its own model/dimension live. Keeping a
hardcoded model→dimension table here made no sense once the model is
chosen dynamically by whatever endpoint is configured."""
from __future__ import annotations


# Pacing tuned against NIM's ~40 RPM ceiling originally — the endpoint now
# owns its own provider-level rate-limit handling internally, so this is a
# conservative default, not a hard requirement. Revisit after the real-scale
# batch test (see EMBEDDING-CURATOR-SOTA-2026-09-12.md) if it's overly slow.
BATCH_SIZE = 50
BATCH_PAUSE_S = 2

# Sparse model id — BM25 via `langchain_qdrant.FastEmbedSparse`. Pure
# CPU, ~zero overhead (tokenization + counting only). Unrelated to the
# dense embedding endpoint above.
SPARSE_MODEL_NAME = "Qdrant/bm25"

# 2026-09-14: `probe()`'s retry budget. `embed_probe_async` itself stays
# a single-shot 20s primitive (the Settings page "Test" button wants
# fast, honest feedback on one attempt) — but `get_embedding_info()`'s
# internal callers (every Qdrant flush) hit it on every cold worker
# process, and a cold embedding endpoint routinely needs longer than
# 20s to answer its first request. Observed live: back-to-back calls
# right after a redeploy both failed within the same ~20s window —
# same cold endpoint, no time given to warm up between them. Mirrors
# `aembed_documents`'s own proven 3-attempt/backoff pattern in this
# same file rather than inventing a new resilience shape.
PROBE_RETRY_ATTEMPTS = 3
PROBE_RETRY_BACKOFF_S = 5
