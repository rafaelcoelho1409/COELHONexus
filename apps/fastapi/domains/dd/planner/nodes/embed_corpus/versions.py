"""embed_corpus cache version — bumped every time the embedding semantics
change (model swap, chunk strategy, normalization) so stored .npz blobs
invalidate cleanly.

v4 (2026-05-25): token-counted chunking — same model, different chunk
boundaries → different per-doc mean-pool results vs v3, so cache must
invalidate.
"""
from __future__ import annotations


CACHE_VERSION = "v4-2026-05-25"
