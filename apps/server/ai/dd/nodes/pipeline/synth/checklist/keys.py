"""checklist_eval — MinIO key builders."""
from __future__ import annotations


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/checklist/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/checklist-latest.json"


def best_blob_key(slug: str, chapter_id: str) -> str:
    """The evaluation of the best-seen iteration (the one render ships); checklist-latest.json is whatever ran last."""
    return f"dd/{slug}/synth/{chapter_id}/checklist-best.json"


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc-latest.json"


def digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/digest-latest.json"
