"""Substep 2 — off_topic: embed every file + drop those below cosine threshold.

NO-OP STUB. Real impl: NIM embedding via rotator, cosine vs framework
descriptor, threshold 0.30 (per zdeprecated planner).
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("off_topic")
async def off_topic(state: PlannerState) -> dict:
    return {"relevant_files": state.get("raw_files") or []}
