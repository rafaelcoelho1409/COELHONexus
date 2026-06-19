from __future__ import annotations


# Bound span attribute size — LangFuse rejects large spans; Tempo charges per byte.
PROMPT_TRUNCATE_CHARS     = 32_000
COMPLETION_TRUNCATE_CHARS = 16_000

# Embedding/rerank record count + first-doc preview, not full payload.
EMBEDDING_PREVIEW_CHARS = 256
RERANK_PREVIEW_CHARS    = 512
