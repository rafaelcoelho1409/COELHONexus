"""Step 9 — render_audit_write: LangGraph node shell.

Materialize chapter markdown + audit + persist. The only synth node with
ZERO LLM calls. Pure deterministic transform + cryptographic vault
round-trip audit.

All orchestration lives in service.render_audit_write_run.

State writes:
  chapter_path  — MinIO key of the README.md (or "" on skip/failure)
  chapter_stats — audit verdict + sizes + counts + cache_hit
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import render_audit_write_run


@traced("render_audit_write")
async def render_audit_write(state: SynthState) -> dict:
    return await render_audit_write_run(state)
