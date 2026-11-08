"""checklist_eval — MinIO key builders."""
from __future__ import annotations


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/checklist/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "checklist")


def best_blob_key(slug: str, chapter_id: str) -> str:
    """The evaluation of the best-seen iteration (the one render ships); checklist-latest.json is whatever ran last."""
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "checklist-best")


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys

    return keys.latest_key(slug, chapter_id, "sawc")


def digest_latest_key(slug: str, chapter_id: str) -> str:
    from ai.dd.graphs.pipeline.synth import keys
    from ai.dd.graphs.pipeline.synth import params

    return keys.latest_key(slug, chapter_id, "ledger" if params.is_v2() else "digest")
