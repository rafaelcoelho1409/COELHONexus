"""Synth v2's isolated code-ledger artifact keys."""
from __future__ import annotations
from ..... import graphs


def latest_blob_key(slug: str, chapter_id: str) -> str:
    return graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "ledger")


def versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"dd/{slug}/synth/{chapter_id}/ledger/{manifest_hash}.json"
