"""sawc — MinIO key builders."""
from __future__ import annotations


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "sawc")


def outline_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "outline")


def digest_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys
    from ai.dd.graphs.pipeline.synth import params

    artifact = "ledger" if params.is_v2() else "digest"
    return keys.latest_key(slug, chapter_id, artifact)


def mgsr_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "mgsr")
