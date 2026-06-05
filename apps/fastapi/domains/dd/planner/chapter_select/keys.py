"""chapter_select — MinIO key builders.

`select_*` keys hold the chapter_select-specific payload (selected
indices, pruning, pinning).

`chapter_plan_*` keys hold the legacy reduce_node-compatible outline so
order_chapters + plan_write read it transparently.
"""
from __future__ import annotations

from .params import BLOB_PREFIX


def select_versioned_key(slug: str, manifest: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/chapter_select/{manifest}.json"


def select_latest_key(slug: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/chapter_select-latest.json"


def chapter_plan_versioned_key(slug: str, manifest: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/chapters/{manifest}.json"


def chapter_plan_latest_key(slug: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/chapter_plan-latest.json"
