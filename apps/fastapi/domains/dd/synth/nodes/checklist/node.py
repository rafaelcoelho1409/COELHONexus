"""Step 7 — checklist_eval: LangGraph node shell.

Binary checklist evaluator (CheckEval EMNLP 2025 + RefineBench Nov 2025
+ 3-layer eval pipeline). Replaces the deprecated 8-dim weighted grader
with 12 binary criteria (deterministic + LLM-judge).

All orchestration lives in service.checklist_eval_run.

State writes:
  checklist_path  — MinIO key of the ChecklistEvaluation blob
  checklist_stats — pass_rate + chapter_passed + failed counts
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import SynthState

from .service import checklist_eval_run


@traced("checklist_eval")
async def checklist_eval(state: SynthState) -> dict:
    return await checklist_eval_run(state)
