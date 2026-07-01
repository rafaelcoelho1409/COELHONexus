"""plan_write node shell — persists the final chapter plan to MinIO."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import plan_write_run


@traced("plan_write")
async def plan_write(state: PlannerState) -> dict:
    return await plan_write_run(state)
