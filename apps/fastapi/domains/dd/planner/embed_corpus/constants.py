"""embed_corpus constants — chunk threshold, prefix, cache version."""

# Token-counted chunking (2026-05-25 — replaces the legacy _CHUNK_CHARS=8000
# heuristic which used only ~25% of the model's 8192-token capacity because
# it conflated chars and tokens).
#
# Llama-3-family BPE tokenizers (which `llama-nemotron-embed-1b-v2` uses)
# vary 2.5-4 chars/token depending on content density. The old char-based
# cap was either over-strict on English (~25% waste) or risked overflow on
# heavy-code chunks. Token-based caps are byte-for-byte correct.
#
# _TOKEN_TARGET = 7800 leaves ~5% headroom (392 tokens) for:
#   - special tokens added by AutoTokenizer (`add_special_tokens=False`
#     prevents most, but NIM may inject input_type prefix server-side)
#   - tokenizer drift across model card revisions
# 8192 is the hard server cap (NIM 400s above it without truncate=END).
_TOKEN_TARGET   = 7800
_TOKEN_HARD_CAP = 8192

# Char-cap kept as a sanity belt for the extreme outlier case where
# tokenizer init fails: per-char-1.0 ratio = absolute worst case → 8192
# chars guarantees ≤ 8192 tokens even for pathological inputs.
_CHUNK_CHARS_FALLBACK = 8000

# Legacy alias kept to avoid breaking imports inside the same package.
# All real chunk-size logic now flows through _TOKEN_TARGET via _chunk_doc.
_CHUNK_CHARS = _CHUNK_CHARS_FALLBACK

_EMBED_PREFIX = "planner"

# Bumped every time the embedding semantics change (model swap, chunk
# strategy, normalization) so stored .npz blobs invalidate cleanly.
# v4 (2026-05-25): token-counted chunking — same model, different chunk
# boundaries → different per-doc mean-pool results vs v3, so cache must
# invalidate.
_CACHE_VERSION = "v4-2026-05-25"
