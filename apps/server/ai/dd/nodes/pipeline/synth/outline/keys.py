"""outline_sdp — MinIO key builders."""
from __future__ import annotations


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/outline/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "outline")
