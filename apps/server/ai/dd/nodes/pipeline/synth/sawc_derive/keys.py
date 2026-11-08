"""sawc_derive — MinIO key builders."""
from __future__ import annotations


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "sawc")


def derive_latest_key(slug: str, chapter_id: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc_derive-latest.json"


def digest_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys
    from ai.dd.graphs.pipeline.synth import params

    return keys.latest_key(slug, chapter_id, "ledger" if params.is_v2() else "digest")


def sawc_versioned_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/sawc/{manifest_hash}.json"
