"""Substep 7 — validate: coverage repair (orphan / hallucinated slug detection).

NO-OP STUB. Real impl: walk chapter_plan, ensure every deduped file is
assigned to exactly one chapter, flag hallucinated slugs (not in the
deduped manifest), auto-merge orphan chapters under <5 files.
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("validate")
async def validate(state: PlannerState) -> dict:
    return {"validated_plan": state.get("chapter_plan") or []}
