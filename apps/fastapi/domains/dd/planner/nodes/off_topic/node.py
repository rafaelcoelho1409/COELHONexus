"""off_topic node shell — LLM KEEP/DROP judge; cosine margins kept in stats for calibration drift detection."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import off_topic_run


@traced("off_topic")
async def off_topic(state: PlannerState) -> dict:
    return await off_topic_run(state)
