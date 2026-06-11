"""Research Radar — MCP sub-server registration.

Pattern: each domain owns a `register(mcp)` function that registers all its
MCP capabilities (tools, resources, prompts, domain-specific middleware) on
the root FastMCP server. Mirrors the apps/fasthtml `features.X.register(rt)`
convention so the three peer apps (fastapi · fasthtml · fastmcp) share a
uniform "register feature on root app" idiom.

This file is intentionally minimal — adding a new tool/resource/prompt to
Research Radar means: (1) add a sub-package under tools/ resources/ prompts/,
(2) add ONE register() call below.
"""
from fastmcp import FastMCP

from .tools.arxiv import tool as arxiv_tool
from .tools.hn import tool as hn_tool
from .tools.huggingface_daily_papers import tool as huggingface_daily_papers_tool
from .tools.semantic_scholar import tool as semantic_scholar_tool


def register(mcp: FastMCP) -> None:
    """Register every Research Radar MCP capability on the root server."""
    arxiv_tool.register(mcp)
    semantic_scholar_tool.register(mcp)
    huggingface_daily_papers_tool.register(mcp)
    hn_tool.register(mcp)
