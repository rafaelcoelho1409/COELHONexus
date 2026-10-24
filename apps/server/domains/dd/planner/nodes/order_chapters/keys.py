"""order_chapters — MinIO key builder."""
from __future__ import annotations
from . import params



def blob_key(slug: str, manifest_hash: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/order/{manifest_hash}.json"
