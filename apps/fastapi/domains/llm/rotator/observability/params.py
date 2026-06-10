from __future__ import annotations


# Truncation caps to bound span attribute size — LangFuse rejects very large
# spans and Tempo charges egress per byte. Captures ~95% of prompts in our
# DD/YCS workloads (median ~5-8 KB, p99 ~25 KB).
PROMPT_TRUNCATE_CHARS     = 32_000
COMPLETION_TRUNCATE_CHARS = 16_000

# Per-batch embedding/rerank input is recorded as a count + first-doc preview,
# not the full payload — embedding batches are 64 docs × ~2KB each.
EMBEDDING_PREVIEW_CHARS = 256
RERANK_PREVIEW_CHARS    = 512
