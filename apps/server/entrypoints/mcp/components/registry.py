"""Register feature-owned MCP components on a composed FastMCP server."""
from __future__ import annotations
from . import bd

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Attach BD tools to the server."""
    bd.registry.register_tools(mcp)


def register_resources(mcp: FastMCP) -> None:
    """Attach BD resources to the server."""
    bd.registry.register_resources(mcp)


def register_prompts(mcp: FastMCP) -> None:
    """Attach BD prompts to the server."""
    bd.registry.register_prompts(mcp)
