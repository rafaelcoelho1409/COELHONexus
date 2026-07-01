"""sawc_derive — LangGraph node; all orchestration in service.sawc_derive_run."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import sawc_derive_run


@traced("sawc_derive")
async def sawc_derive(state: SynthState) -> dict:
    return await sawc_derive_run(state)
