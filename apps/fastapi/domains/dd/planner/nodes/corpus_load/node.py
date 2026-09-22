"""corpus_load node shell — inventories the ingested corpus; bodies stay in MinIO (pointers only to avoid checkpoint bloat)."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced
from . import service


@traced("corpus_load")
async def corpus_load(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.corpus_load_run(state)
