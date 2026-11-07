"""sawc_derive — MinIO key builders."""
from __future__ import annotations


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc-latest.json"


def derive_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc_derive-latest.json"


def digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/digest-latest.json"


def sawc_versioned_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc/{manifest_hash}.json"
