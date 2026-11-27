"""Register feature-owned MCP components on a composed FastMCP server."""

from __future__ import annotations
from . import bd
from . import ycs

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Attach domain-owned tools to the server."""
    bd.registry.register_tools(mcp)
    ycs.registry.register_tools(mcp)


def register_resources(mcp: FastMCP) -> None:
    """Attach domain-owned resources to the server."""
    bd.registry.register_resources(mcp)
    ycs.registry.register_resources(mcp)


def register_prompts(mcp: FastMCP) -> None:
    """Attach domain-owned prompts to the server."""
    bd.registry.register_prompts(mcp)
    ycs.registry.register_prompts(mcp)


