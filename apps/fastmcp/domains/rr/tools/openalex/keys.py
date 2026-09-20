"""Identifier registries for the OpenAlex tool — per docs/CODE-CONVENTIONS.md §2."""
from __future__ import annotations


# MCP tool name — shared by tool.py's registration calls (rate limiter,
# @mcp.tool) so the two can't drift.
TOOL_NAME: str = "openalex_search"
