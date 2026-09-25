"""FastMCP Prompts for Research Radar.

Prompts are USER-INVOCABLE templated strings — different from internal
agent system_prompts. A FastHTML "command palette" or an external
MCP-aware client (Claude Desktop, MCP Inspector) can fetch + run them.

We expose one:

  /digest_today    Returns a parameterized prompt asking for "what's
                   notable in today's research." Useful for ad-hoc
                   scans without writing the full scan request.

Layout (per docs/CODE-CONVENTIONS.md §4):
  keys.py      prompt-name registry (DIGEST_TODAY)
  prompts.py   template strings
  versions.py  version markers (cache-invalidation knobs)
  domain.py    PURE build/validate helpers
  prompt.py    thin `@mcp.prompt` boundary (one wrapper per prompt)
  __init__.py  ← THIS — re-exports + register(mcp) dispatch only

Architecture-doc §2.2.2.
"""
from __future__ import annotations
from . import domain, keys, prompt, prompts, versions

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


__all__ = ["domain", "keys", "prompt", "prompts", "versions"]


def register(mcp: FastMCP) -> None:
    """Register all RR prompts on the root server."""
    prompt.register(mcp)
