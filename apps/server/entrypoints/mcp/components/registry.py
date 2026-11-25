"""Register feature-owned MCP components on a composed FastMCP server."""
from __future__ import annotations
from . import bd

from fastmcp import FastMCP


def register_tools(
    mcp: FastMCP,
    *,
    include_bd: bool = True,
    trusted_in_process: bool = False,
) -> None:
    """Attach enabled feature components to the server."""
    if include_bd:
        bd.registry.register_tools(mcp, trusted_in_process = trusted_in_process)
