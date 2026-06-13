"""Research Radar — MCP sub-server registration.

Pattern: each domain owns a `register(mcp)` function that registers all its
MCP capabilities (tools, resources, prompts, domain-specific middleware) on
the root FastMCP server. Mirrors the apps/fasthtml `features.X.register(rt)`
convention so the three peer apps (fastapi · fasthtml · fastmcp) share a
uniform "register feature on root app" idiom.

Step-7 (2026-06-12): Resources + Prompts added (architecture-doc §2.2).
The radar repo now exercises all 4 FastMCP primitives that ship in v3.x:

  Tools     — arxiv_search, semantic_scholar_search, huggingface_daily_papers, hn_search
  Resources — radar://latest_digest, radar://concept/{name}
  Prompts   — /digest_today
  Auth      — TODO (JWT verifier — only matters when we expose
              externally; ClusterIP is enough today)
  Composition — TODO (mount() — only matters when a 2nd domain exposes
              MCP tools, e.g. DD-as-MCP)
"""
from fastmcp import FastMCP

from .prompts import register as register_prompts
from .resources import register as register_resources
from .tools.arxiv import tool as arxiv_tool
from .tools.hn import tool as hn_tool
from .tools.huggingface_daily_papers import tool as huggingface_daily_papers_tool
from .tools.semantic_scholar import tool as semantic_scholar_tool


def register(mcp: FastMCP) -> None:
    """Register every Research Radar MCP capability on the root server."""
    # Tools (4)
    arxiv_tool.register(mcp)
    semantic_scholar_tool.register(mcp)
    huggingface_daily_papers_tool.register(mcp)
    hn_tool.register(mcp)
    # Resources (2)
    register_resources(mcp)
    # Prompts (1)
    register_prompts(mcp)
    # TODO Auth + Composition: see module docstring.
