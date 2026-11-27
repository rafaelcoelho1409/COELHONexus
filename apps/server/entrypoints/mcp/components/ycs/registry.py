"""Register FastMCP capabilities owned by the YCS domain."""
from __future__ import annotations
from . import prompts, resources, tools

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Register YCS Ask and Query tools."""
    tools.registry.register_tools(mcp)


def register_resources(mcp: FastMCP) -> None:
    """Register YCS Ask and Query context resources."""
    resources.registry.register_resources(mcp)


def register_prompts(mcp: FastMCP) -> None:
    """Register YCS Ask and Query workflow prompts."""
    prompts.registry.register_prompts(mcp)
