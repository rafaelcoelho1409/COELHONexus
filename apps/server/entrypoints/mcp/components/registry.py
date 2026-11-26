"""Register feature-owned MCP components on a composed FastMCP server."""
from __future__ import annotations

from fastmcp import FastMCP

from . import bd


def register_tools(
    mcp: FastMCP,
    *,
    include_bd: bool = True,
    trusted_in_process: bool = False,
) -> None:
    """Attach enabled feature components to the server."""
    if include_bd:
        bd.registry.register_tools(mcp, trusted_in_process = trusted_in_process)


def register_resources(
    mcp: FastMCP,
    *,
    include_bd: bool = True,
    trusted_in_process: bool = False,
) -> None:
    """Attach enabled resource features to the server."""
    if include_bd:
        bd.registry.register_resources(mcp, trusted_in_process = trusted_in_process)


def register_prompts(mcp: FastMCP, *, include_bd: bool = True) -> None:
    """Attach enabled prompt features to the server."""
    if include_bd:
        bd.registry.register_prompts(mcp)
