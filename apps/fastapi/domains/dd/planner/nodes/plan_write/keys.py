"""plan_write — MinIO key builders."""
from __future__ import annotations

from .params import BLOB_PREFIX


def versioned_blob_key(slug: str, manifest_hash: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/plan/{manifest_hash}.json"


def latest_blob_key(slug: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/plan-latest.json"
