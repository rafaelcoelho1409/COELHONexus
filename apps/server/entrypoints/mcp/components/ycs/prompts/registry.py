"""Register YCS Ask and Query prompt features."""
from __future__ import annotations
from . import ask, query

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    ask.registry.register_prompts(mcp)
    query.registry.register_prompts(mcp)
