"""FastMCP Prompts for Research Radar.

Prompts are USER-INVOCABLE templated strings — different from internal
agent system_prompts. A FastHTML "command palette" or an external
MCP-aware client (Claude Desktop, MCP Inspector) can fetch + run them.

We expose one:

  /digest_today    Returns a parameterized prompt asking for "what's
                   notable in today's research." Useful for ad-hoc
                   scans without writing the full scan request.

Architecture-doc §2.2.2.
"""
from fastmcp import FastMCP

from . import digest_today


def register(mcp: FastMCP) -> None:
    """Register all RR prompts on the root server."""
    digest_today.register(mcp)
