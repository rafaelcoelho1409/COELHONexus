"""embed_corpus tunables — token cap, prefix, cache version."""
from __future__ import annotations


# 7800 leaves 5% headroom for NIM server-side prefix injection and tokenizer drift; 8192 is the hard NIM cap (400s above it).
TOKEN_TARGET = 7800
TOKEN_HARD_CAP = 8192

# Char-cap kept as a sanity belt for the extreme outlier case where
# tokenizer init fails: per-char-1.0 ratio = absolute worst case → 8192
# chars guarantees ≤ 8192 tokens even for pathological inputs.
CHUNK_CHARS_FALLBACK = 8000

EMBED_PREFIX = "planner"
