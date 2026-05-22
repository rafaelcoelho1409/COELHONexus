"""embed_corpus constants — chunk threshold, prefix, cache version."""

# Threshold for the chunk-and-pool path. Below this, embed the whole doc
# in one shot; above this, chunk to bound NIM batch payload. 8 KB is the
# v1-validated truncation cap; using it as a chunk-size keeps per-chunk
# behavior identical to the previous truncate-then-embed path while
# preserving signal from the rest of the document.
_CHUNK_CHARS = 8000
_EMBED_PREFIX = "planner"
# Bumped every time the embedding semantics change (model swap, chunk
# strategy, normalization) so stored .npz blobs invalidate cleanly.
_CACHE_VERSION = "v2-2026-05-17"
