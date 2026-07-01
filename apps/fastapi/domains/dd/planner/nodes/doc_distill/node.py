"""doc_distill node shell — parallel distillation with deterministic fallback so no content-bearing doc is silently dropped."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import doc_distill_run


@traced("doc_distill")
async def doc_distill(state: PlannerState) -> dict:
    return await doc_distill_run(state)
