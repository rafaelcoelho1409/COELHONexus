"""digest_construct — LangGraph node."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import digest_construct_run


@traced("digest_construct")
async def digest_construct(state: SynthState) -> dict:
    return await digest_construct_run(state)
