"""MinIO key builders; chapter_plan_* keys use the legacy reduce_node-compatible schema so downstream nodes read transparently."""
from __future__ import annotations
from . import params



def select_versioned_key(slug: str, manifest: str) -> str:
    return f"dd/{slug}/planner/chapter_select/{manifest}.json"


def select_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/chapter_select-latest.json"


def chapter_plan_versioned_key(slug: str, manifest: str) -> str:
    return f"dd/{slug}/planner/chapters/{manifest}.json"


def chapter_plan_latest_key(slug: str) -> str:
    return f"dd/{slug}/planner/chapter_plan-latest.json"
