"""Register FastMCP capabilities owned by the BD domain."""
from __future__ import annotations
from . import tools

from fastmcp import FastMCP


def register_tools(mcp: FastMCP, *, trusted_in_process: bool = False) -> None:
    """Register BD-owned tool features."""
    tools.search.registry.register_tools(mcp, trusted_in_process = trusted_in_process)
