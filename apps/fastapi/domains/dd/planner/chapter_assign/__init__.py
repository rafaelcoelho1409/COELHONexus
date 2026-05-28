"""chapter_assign — per-doc LLM scores membership against each proposal.

Parallel LLM call per doc returns a confidence score (0.0-1.0) per
chapter proposal. Multi-assignment allowed (a doc can score >threshold
on multiple chapters; chapter_select breaks ties via coverage greedy).

See docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md.
"""
from .node import chapter_assign, load_assignments

__all__ = ["chapter_assign", "load_assignments"]
