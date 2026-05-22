"""embed_corpus subpackage — planner substep 2."""
from .constants import _CACHE_VERSION, _CHUNK_CHARS, _EMBED_PREFIX
from .node import embed_corpus
from .service import (
    _attach_otel_attrs,
    _blob_key,
    _chunk_doc,
    _l2_normalize,
    _manifest_hash,
    _pack_npz,
    load_embeddings,
)

__all__ = [
    "_CACHE_VERSION",
    "_CHUNK_CHARS",
    "_EMBED_PREFIX",
    "_attach_otel_attrs",
    "_blob_key",
    "_chunk_doc",
    "_l2_normalize",
    "_manifest_hash",
    "_pack_npz",
    "embed_corpus",
    "load_embeddings",
]
