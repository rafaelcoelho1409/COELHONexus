"""doc_distill node shell — parallel distillation with deterministic fallback so no content-bearing doc is silently dropped."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced
from . import service


@traced("doc_distill")
async def doc_distill(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.doc_distill_run(state)
