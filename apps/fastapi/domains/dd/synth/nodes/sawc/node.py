"""sawc_write — LangGraph node; all orchestration in service.sawc_write_run."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import sawc_write_run


@traced("sawc_write")
async def sawc_write(state: SynthState) -> dict:
    return await sawc_write_run(state)
