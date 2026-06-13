"""Prompt: `/digest_today`

A USER-invokable templated prompt that returns a parameterized scan
request the operator can paste into FastHTML or feed into another agent.

Typical use: an external MCP-aware client (Claude Desktop, MCP Inspector,
a portable agent harness) calls `list_prompts()` → sees `digest_today` →
calls it with `topic='deep agents'` → gets back the prompt string →
passes that string to its own agent runtime.
"""
from __future__ import annotations

from fastmcp import FastMCP


_TEMPLATE = """\
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


def register(mcp: FastMCP) -> None:
    """Register `/digest_today` on the root server."""

    @mcp.prompt(name="digest_today")
    def digest_today(
        topic:     str = "deep agents",
        verticals: str = "cs.LG, cs.AI",
        top_n:     int = 8,
    ) -> str:
        """Generate a Research Radar scan request for today's topic.

        Args:
            topic: 2-5 word topical phrase (e.g. 'constrained decoding').
            verticals: Comma-separated category list (cs.LG, cs.AI, q-fin.PR).
            top_n: How many papers from triage to deep-read. 4-30.
        """
        return _TEMPLATE.format(
            topic     = topic.strip() or "deep agents",
            verticals = verticals.strip() or "cs.LG, cs.AI",
            top_n     = max(4, min(30, int(top_n))),
        )
