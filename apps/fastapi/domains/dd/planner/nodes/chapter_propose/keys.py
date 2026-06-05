"""chapter_propose — MinIO key builders."""
from __future__ import annotations

from .params import BLOB_PREFIX


def versioned_key(slug: str, manifest: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/chapter_propose/{manifest}.json"


def latest_key(slug: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/chapter_propose-latest.json"
