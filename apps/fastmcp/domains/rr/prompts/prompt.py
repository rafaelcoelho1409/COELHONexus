"""RR prompt boundary — thin `@mcp.prompt` shell.

Per docs/CODE-CONVENTIONS.md §4: decorator binding only. Template text
lives in `prompts.py`, names in `keys.py`, rendering in `domain.py`.
Grows by appending one wrapper per prompt; split only if this file
passes ~100 lines or a prompt gains I/O.
"""
from __future__ import annotations
from . import domain, keys

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register all RR prompts on the root server."""

    @mcp.prompt(name=keys.DIGEST_TODAY)
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
        return domain.build_digest_today_prompt(topic, verticals, top_n)
