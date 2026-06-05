"""Step 8 — mgsr_replan: LangGraph node shell.

Memory-Guided Structure Replanner (SurveyGen-I + CoRefine). Trivial-pass
fast path for chapters that already passed the checklist; LLM-driven
structured replan actions on failure. v1 emits actions + halt decision
but does NOT loop (the StateGraph cycle is wired by graph.py's
conditional edge after mgsr_replan).

All orchestration lives in service.mgsr_replan_run.

State writes:
  mgsr_path             — MinIO key of the MGSRReplan blob (latest pointer)
  mgsr_stats            — halt/reason/confidence/n_actions/wall_ms + cache_hit
  prev_checklist_score  — for plateau detection on next iteration
  best_seen_*           — OP-12 best-seen-rescue carried across iters
"""
from __future__ import annotations

from ..observability import traced
from ..state import SynthState

from .service import mgsr_replan_run


@traced("mgsr_replan")
async def mgsr_replan(state: SynthState) -> dict:
    return await mgsr_replan_run(state)
