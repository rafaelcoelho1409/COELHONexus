"""embed_corpus tunables — token cap, prefix, cache version."""
from __future__ import annotations


# Token-counted chunking: Llama BPE varies 2.5-4 chars/token; the legacy char-based cap (CHUNK_CHARS=8000) used only ~25% of model capacity and risked overflow on heavy-code chunks.
# TOKEN_TARGET = 7800 leaves ~5% headroom (392 tokens) for:
#   - special tokens added by AutoTokenizer (`add_special_tokens=False`
#     prevents most, but NIM may inject input_type prefix server-side)
#   - tokenizer drift across model card revisions
# 8192 is the hard server cap (NIM 400s above it without truncate=END).
TOKEN_TARGET = 7800
TOKEN_HARD_CAP = 8192

# Char-cap kept as a sanity belt for the extreme outlier case where
# tokenizer init fails: per-char-1.0 ratio = absolute worst case → 8192
# chars guarantees ≤ 8192 tokens even for pathological inputs.
CHUNK_CHARS_FALLBACK = 8000

EMBED_PREFIX = "planner"
