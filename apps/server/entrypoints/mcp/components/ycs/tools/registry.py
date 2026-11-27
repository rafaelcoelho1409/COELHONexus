"""Register YCS Ask and Query tool features."""
from __future__ import annotations
from . import ask, query

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    ask.registry.register_tools(mcp)
    query.registry.register_tools(mcp)
