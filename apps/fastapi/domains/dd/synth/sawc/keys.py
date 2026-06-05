"""sawc — MinIO key builders."""
from __future__ import annotations

from .params import BLOB_PREFIX


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/sawc/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def outline_latest_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/outline-latest.json"


def digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/digest-latest.json"
