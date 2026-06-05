"""Substep 7 — chapter_select: LangGraph node shell.

Greedy coverage selection over proposed chapters (no LLM). Picks the
minimum chapter set covering ≥95% of docs above confidence threshold,
hard-pinning structurally-seeded chapters and pruning <3-doc chapters
unless pinned. Output schema matches the legacy reduce_node so
downstream order_chapters + plan_write need no changes.

All orchestration lives in service.chapter_select_run.

State writes:
  chapter_plan_ref — MinIO key of the JSON (same field as legacy reduce)
  select_stats     — counts, coverage, pruned, etc.
"""
from __future__ import annotations

from ..observability import traced
from ..state import PlannerState

from .service import chapter_select_run


@traced("chapter_select")
async def chapter_select(state: PlannerState) -> dict:
    return await chapter_select_run(state)
