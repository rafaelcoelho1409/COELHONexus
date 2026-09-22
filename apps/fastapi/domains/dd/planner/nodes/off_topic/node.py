"""off_topic node shell — LLM KEEP/DROP judge; cosine margins kept in stats for calibration drift detection."""
from __future__ import annotations
import domains
from domains.dd.planner.runtime.observability.service import traced
from . import service


@traced("off_topic")
async def off_topic(state: domains.dd.planner.state.PlannerState) -> dict:
    return await service.off_topic_run(state)
