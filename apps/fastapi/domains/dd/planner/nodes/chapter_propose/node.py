"""Substep 5 — chapter_propose: LangGraph node shell.

LLM proposes ~target_chapters_for_n_docs() chapters covering the corpus
surface (adaptive to corpus size; v2 2026-05-31). Pipeline lives in
service.chapter_propose_run.

State writes:
  chapter_proposals_ref — MinIO key of the JSON
  propose_stats         — counts + chosen titles for UI
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import chapter_propose_run


@traced("chapter_propose")
async def chapter_propose(state: PlannerState) -> dict:
    return await chapter_propose_run(state)
