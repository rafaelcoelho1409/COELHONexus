"""render — MinIO key builders."""
from __future__ import annotations
import domains
from . import params, versions



def normalize_cache_key(slug: str, vault_hash: str) -> str:
    return f"synth-vault/{slug}/normalized/{versions.NORMALIZE_PROMPT_VERSION}/{vault_hash}.txt"


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/render/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/render-latest.json"


def artifact_key(slug: str, chapter_id: str, artifact_name: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/{artifact_name}"


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def mgsr_latest_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/mgsr-latest.json"


def planner_latest_key(slug: str) -> str:
    """The planner's latest plan blob — same key the planner writes and the
    synth/api read (canonical builder: domains.dd.planner.keys)."""
    return domains.dd.planner.keys.plan_latest_key(slug)


def source_key_to_vault_key(source_key: str, framework_slug: str) -> str:
    """Translate ingestion page key to vault key; mirrors storage_minio.vault_manifest_key() without importing it."""
    basename = source_key.rstrip("/").rsplit("/", 1)[-1]
    if basename.endswith(".md"):
        basename = basename[:-3]
    return f"synth-vault/{framework_slug}/pages/{basename}.vault.json"
