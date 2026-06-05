"""sawc_derive — LangGraph node shell.

Ship #95 (2026-05-24). Runs AFTER sawc_write commits its chapter blob
and BEFORE checklist_eval. Scans the just-written sections for subtopics
whose vault entry is signature-only / too thin to teach effectively,
then fires Analogical-Prompting + MPSC (Multi-Path Self-Consistency,
arXiv 2503.04611) to generate runnable derived examples.

All orchestration lives in service.sawc_derive_run.

State writes:
  derive_stats — DeriveStats dict (counts + per-subtopic attempt records)
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import sawc_derive_run


@traced("sawc_derive")
async def sawc_derive(state: SynthState) -> dict:
    return await sawc_derive_run(state)
