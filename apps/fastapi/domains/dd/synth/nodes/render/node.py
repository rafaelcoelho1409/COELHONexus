"""render_audit_write — LangGraph node; the render/audit/persist path itself is a deterministic vault round-trip with zero LLM calls, but the vault-normalize pass (_normalize_vault_codes) does call the Rotator once per unique code hash, cached forever after — see service.py. All orchestration in service.render_audit_write_run."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import render_audit_write_run


@traced("render_audit_write")
async def render_audit_write(state: SynthState) -> dict:
    return await render_audit_write_run(state)
