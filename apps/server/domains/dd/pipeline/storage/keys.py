"""MinIO key builders for the planner's artifacts (`dd/{slug}/planner/...`); the keys are stored data, so they keep the stage name."""
from __future__ import annotations


def planner_prefix(slug: str) -> str:
    """Every MinIO object of a framework's planner output (what a full wipe of the framework removes)."""
    return f"dd/{slug}/planner/"


def planner_timing_key(slug: str) -> str:
    """The persisted planner timing roll-up; survives a UI refresh so the total wall-clock shows for a finished run."""
    return f"dd/{slug}/planner/planner-timing-latest.json"


def plan_latest_key(slug: str) -> str:
    """The latest finalized plan (chapter outline + sources); read by synth's run-startup, the API and the debug routes."""
    return f"dd/{slug}/planner/plan-latest.json"


def plan_versioned_key(slug: str, manifest_hash: str) -> str:
    return f"dd/{slug}/planner/plan/{manifest_hash}.json"


def distill_versioned_key(slug: str, manifest: str) -> str:
    return f"dd/{slug}/planner/doc_distill/{manifest}.json"


def distill_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/doc_distill-latest.json"


def proposals_versioned_key(slug: str, manifest: str) -> str:
    return f"dd/{slug}/planner/chapter_propose/{manifest}.json"


def proposals_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/chapter_propose-latest.json"


def assignments_versioned_key(slug: str, manifest: str) -> str:
    return f"dd/{slug}/planner/chapter_assign/{manifest}.json"


def assignments_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/chapter_assign-latest.json"


def select_versioned_key(slug: str, manifest: str) -> str:
    return f"dd/{slug}/planner/chapter_select/{manifest}.json"


def select_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/chapter_select-latest.json"


def chapter_plan_versioned_key(slug: str, manifest: str) -> str:
    """The reduce-compatible outline chapter_select writes; downstream nodes read it under this legacy schema."""
    return f"dd/{slug}/planner/chapters/{manifest}.json"


def chapter_plan_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/chapter_plan-latest.json"


def order_key(slug: str, manifest_hash: str) -> str:
    return f"dd/{slug}/planner/order/{manifest_hash}.json"


def item_prefix(slug: str, node: str) -> str:
    """The per-item results of a fan-out node (one small JSON per page), content-addressed so a re-plan reuses what did not change."""
    return f"dd/{slug}/planner/items/{node}/"


def item_key(slug: str, node: str, version: str, digest: str) -> str:
    return f"{item_prefix(slug, node)}{version}/{digest}.json"


# ---- synth: `dd/{slug}/synth/...` (stored data, so the keys keep the stage name) ----------------------------------------------------------

def synth_prefix(slug: str) -> str:
    """Every MinIO object of a framework's synth output (what a full wipe of the framework removes)."""
    return f"dd/{slug}/synth/"


def synth_chapter_prefix(slug: str, chapter_id: str) -> str:
    return f"{synth_prefix(slug)}{chapter_id}/"


def synth_item_prefix(slug: str, chapter_id: str, node: str) -> str:
    """The per-item results of a synth fan-out node (one JSON per source, section or code block), content-addressed so a resumed
    chapter reuses what an interrupted run finished. Under the chapter's prefix: a chapter wipe removes them."""
    return f"{synth_chapter_prefix(slug, chapter_id)}items/{node}/"


def synth_item_key(slug: str, chapter_id: str, node: str, version: str, digest: str) -> str:
    return f"{synth_item_prefix(slug, chapter_id, node)}{version}/{digest}.json"


def study_timing_key(slug: str) -> str:
    """MinIO key for study timing roll-up — persisted so totals survive a UI refresh."""
    return f"{synth_prefix(slug)}study-timing-latest.json"


def chapter_readme_key(slug: str, chapter_id: str) -> str:
    return f"{synth_chapter_prefix(slug, chapter_id)}README.md"


def chapter_render_latest_key(slug: str, chapter_id: str) -> str:
    return f"{synth_chapter_prefix(slug, chapter_id)}render-latest.json"


def chapter_artifact_key(slug: str, chapter_id: str, artifact_name: str) -> str:
    return f"{synth_chapter_prefix(slug, chapter_id)}{artifact_name}"


def book_harmonize_versioned_key(slug: str, manifest_hash: str) -> str:
    return f"{synth_prefix(slug)}book_harmonize/{manifest_hash}.json"


def book_harmonize_latest_key(slug: str) -> str:
    return f"{synth_prefix(slug)}book_harmonize-latest.json"
