"""order_chapters — MinIO key builder."""
from __future__ import annotations

from .params import BLOB_PREFIX


def blob_key(slug: str, manifest_hash: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/order/{manifest_hash}.json"
