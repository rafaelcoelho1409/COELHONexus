"""Substep 1 — corpus_load: LangGraph node shell.

Inventories the framework's ingested corpus and produces:

  state.raw_files     — list of MinIO keys (one per page; pointers only;
                        bodies stay in MinIO and load on demand).
  state.corpus_stats  — observability dict (count/bytes/percentiles +
                        load wall-clock); consumed by the FastHTML
                        substep card AND attached as OTel span attributes
                        for the LangFuse trace.

All orchestration lives in service.corpus_load_run.
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import corpus_load_run


@traced("corpus_load")
async def corpus_load(state: PlannerState) -> dict:
    return await corpus_load_run(state)
