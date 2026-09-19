"""plan_write node shell — persists the final chapter plan to MinIO."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced

from . import service


@traced("plan_write")
async def plan_write(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.plan_write_run(state)
