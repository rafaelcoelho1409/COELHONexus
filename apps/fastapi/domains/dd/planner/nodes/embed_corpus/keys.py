"""embed_corpus — MinIO key builder."""
from __future__ import annotations

from .params import EMBED_PREFIX


def blob_key(slug: str, manifest_hash: str) -> str:
    return f"{EMBED_PREFIX}/{slug}/embeddings/{manifest_hash}.npz"
