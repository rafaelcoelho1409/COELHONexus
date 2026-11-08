"""System prompts of the planner's agents, one folder per role."""
from __future__ import annotations

from . import chapter_architect, chapter_curator, chapter_designer, chapter_sequencer, page_card, page_distiller, page_judge, page_scorer, plan_critic, section_analyst

__all__ = [
    "chapter_architect",
    "chapter_curator",
    "chapter_designer",
    "chapter_sequencer",
    "page_distiller",
    "page_card",
    "page_judge",
    "page_scorer",
    "plan_critic",
    "section_analyst",
]
