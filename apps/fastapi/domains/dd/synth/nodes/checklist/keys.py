"""checklist_eval — MinIO key builders."""
from __future__ import annotations
from . import params, versions



def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/checklist/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/checklist-latest.json"


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/digest-latest.json"


def cocoa_abstraction_key(hash_: str) -> str:
    """Stage-1 cache keyed on vault hash + prompt_version; prompt revision auto-invalidates."""
    return f"synth-cache/cocoa-abstractions/{versions.COCOA_PROMPT_VERSION}/{hash_}.json"


def atomic_claim_key(prose_hash: str) -> str:
    return f"synth-cache/atomic-claims/{versions.ATOMIC_CLAIM_PROMPT_VERSION}/{prose_hash}.json"
