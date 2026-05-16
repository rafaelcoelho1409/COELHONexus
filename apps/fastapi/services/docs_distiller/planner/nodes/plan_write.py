"""Substep 8 — plan_write: persist final plan to MinIO.

NO-OP STUB. Real impl: write validated_plan as JSON under
`ingestion/{slug}/plan.json` so synth nodes downstream can read it.
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("plan_write")
async def plan_write(state: PlannerState) -> dict:
    return {"plan_path": "", "status": "done"}
