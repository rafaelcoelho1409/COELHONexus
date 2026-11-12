"""mgsr — MinIO key builders."""
from __future__ import annotations

from ..... import graphs


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/mgsr/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "mgsr")


def checklist_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "checklist")


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "sawc")


def outline_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "outline")
