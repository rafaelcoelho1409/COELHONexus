"""render — MinIO key builders."""
from __future__ import annotations

from .params import BLOB_PREFIX


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/render/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/render-latest.json"


def artifact_key(slug: str, chapter_id: str, artifact_name: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/{artifact_name}"


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def mgsr_latest_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/mgsr-latest.json"


def planner_latest_key(slug: str) -> str:
    return f"planner/{slug}/plan-latest.json"


def source_key_to_vault_key(source_key: str, framework_slug: str) -> str:
    """Translate ingestion page key to vault key; mirrors storage_minio.vault_manifest_key() without importing it."""
    basename = source_key.rstrip("/").rsplit("/", 1)[-1]
    if basename.endswith(".md"):
        basename = basename[:-3]
    return f"synth-vault/{framework_slug}/pages/{basename}.vault.json"
