"""render — MinIO key builders."""
from __future__ import annotations
import domains
from . import versions
from ..... import graphs



def normalize_cache_key(slug: str, vault_hash: str) -> str:
    return f"dd/{slug}/synth-cache/normalized/{versions.NORMALIZE_PROMPT_VERSION}/{vault_hash}.txt"


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/render/{manifest_hash}.json"


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "render")


def artifact_key(slug: str, chapter_id: str, artifact_name: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/{artifact_name}"


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "sawc")


def mgsr_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "mgsr")


def planner_latest_key(slug: str) -> str:
    """The planner's latest plan blob — same key the planner writes and the
    synth/api read (canonical builder: domains.dd.pipeline.storage.keys)."""
    return domains.dd.pipeline.storage.keys.plan_latest_key(slug)


def source_key_to_vault_key(source_key: str, framework_slug: str) -> str:
    """Translate ingestion page key to vault key; mirrors storage_minio.vault_manifest_key() without importing it."""
    basename = source_key.rstrip("/").rsplit("/", 1)[-1]
    if basename.endswith(".md"):
        basename = basename[:-3]
    run_id = domains.dd.ingestion.storage.keys.run_id_from_page_key(source_key)
    prefix = domains.dd.ingestion.storage.keys.vault_prefix(framework_slug, run_id)
    return f"{prefix}pages/{basename}.vault.json"


def checklist_latest_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "checklist")


def checklist_best_key(slug: str, chapter_id: str) -> str:
    """The evaluation of the best-seen iteration (the one render ships); checklist-latest.json is whatever ran last."""
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "checklist-best")
