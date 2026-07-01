"""outline_sdp — LangGraph node; all orchestration in service.outline_sdp_run."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import outline_sdp_run


@traced("outline_sdp")
async def outline_sdp(state: SynthState) -> dict:
    return await outline_sdp_run(state)
