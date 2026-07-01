"""render_audit_write — LangGraph node; zero LLM calls, deterministic vault round-trip. All orchestration in service.render_audit_write_run."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import render_audit_write_run


@traced("render_audit_write")
async def render_audit_write(state: SynthState) -> dict:
    return await render_audit_write_run(state)
