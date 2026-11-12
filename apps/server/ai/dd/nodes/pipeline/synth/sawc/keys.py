"""sawc — MinIO key builders."""
from __future__ import annotations

from ..... import graphs


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "sawc")


def outline_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "outline")


def digest_latest_key(slug: str, chapter_id: str) -> str:
    artifact = "ledger" if graphs.pipeline.synth.params.is_v2() else "digest"
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, artifact)


def mgsr_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "mgsr")
