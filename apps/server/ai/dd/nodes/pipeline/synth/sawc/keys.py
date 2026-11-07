"""sawc — MinIO key builders."""
from __future__ import annotations


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc-latest.json"


def outline_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/outline-latest.json"


def digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/digest-latest.json"


def mgsr_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/mgsr-latest.json"
