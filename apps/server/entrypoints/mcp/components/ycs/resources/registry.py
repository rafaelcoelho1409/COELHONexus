"""Register YCS Ask and Query resources."""
from __future__ import annotations
from . import ask, query

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    ask.registry.register_resources(mcp)
    query.registry.register_resources(mcp)
