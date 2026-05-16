"""Substep 6 — reduce: single LLM merge of shard results → chapter plan.

NO-OP STUB. Real impl: build corpus summary from shard_results, single
LLM call merges into 4-12 chapters with file assignments + chapter
titles + ordering rationale.
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("reduce")
async def reduce_node(state: PlannerState) -> dict:
    return {"chapter_plan": []}
