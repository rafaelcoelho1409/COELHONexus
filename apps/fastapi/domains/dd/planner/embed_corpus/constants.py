"""embed_corpus constants — chunk threshold, prefix, cache version."""

# Threshold for the chunk-and-pool path. Below this, embed the whole doc
# in one shot; above this, chunk to bound NIM batch payload. 8 KB is the
# v1-validated truncation cap; using it as a chunk-size keeps per-chunk
# behavior identical to the previous truncate-then-embed path while
# preserving signal from the rest of the document.
# 2026-05-23 night — REVERTED to 8000 alongside the embedder rollback
# (llama-nemotron-embed-1b-v2's hard cap is 8192 tokens, ~8K chars at the
# default char-per-token ratio). When a verified 32K-context NIM embedder
# is in place, bump this back to 28000.
_CHUNK_CHARS = 8000
_EMBED_PREFIX = "planner"
# Bumped every time the embedding semantics change (model swap, chunk
# strategy, normalization) so stored .npz blobs invalidate cleanly.
# v3 = adds content-NFC normalization (still applied; helps with cache
# hits across runs where source content has whitespace drift). The Phase B
# embedder swap was reverted but the normalize_content() call stays as it
# yields cleaner embedding inputs regardless of which model is in use.
_CACHE_VERSION = "v3-2026-05-23"
