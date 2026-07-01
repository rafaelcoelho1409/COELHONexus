"""corpus_load node shell — inventories the ingested corpus; bodies stay in MinIO (pointers only to avoid checkpoint bloat)."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import corpus_load_run


@traced("corpus_load")
async def corpus_load(state: PlannerState) -> dict:
    return await corpus_load_run(state)
