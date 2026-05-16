"""Substep 3 — dedup: MinHash + Jaccard threshold 0.85 to drop near-dupes.

NO-OP STUB. Real impl: code-aware MinHash with token vault preservation
(zdeprecated `_dedup_chapter_files`).
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("dedup")
async def dedup(state: PlannerState) -> dict:
    return {"deduped_files": state.get("relevant_files") or []}
