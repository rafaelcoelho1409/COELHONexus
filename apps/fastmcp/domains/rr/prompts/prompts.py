"""RR prompt templates — per docs/CODE-CONVENTIONS.md §2.

`prompts.py` holds LLM prompt strings, nothing else. Version markers live
in `versions.py`, rendering/validation in `domain.py`, the `@mcp.prompt`
binding in `__init__.py:register`.
"""
from __future__ import annotations


DIGEST_TODAY_TEMPLATE: str = """\
Run a Research Radar scan with these parameters:

Topic:     {topic}
Verticals: {verticals}
Top N:     {top_n}

Execute the full 5-phase pipeline (discovery → triage → deep_read →
graph_build → synthesis) and surface:

1. The top {top_n} papers ranked by signal_score.
2. 3-5 emerging themes per the cross_paper_synthesis skill.
3. For each paper: a 1-sentence summary + the deep_read extraction's
   `money_angle` (the commercial / portfolio applicability).

If today's HuggingFace Daily Papers feed is empty, skip it gracefully
(triage tolerates per-source zeros).
"""
