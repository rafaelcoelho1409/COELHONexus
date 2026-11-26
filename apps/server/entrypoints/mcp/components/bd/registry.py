"""Register FastMCP capabilities owned by the BD domain."""
from __future__ import annotations
from . import prompts, resources, tools

from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Register BD-owned tool features."""
    tools.registry.register_tools(mcp)


def register_resources(mcp: FastMCP) -> None:
    """Register BD-owned run and index resources."""
    resources.registry.register_resources(mcp)


def register_prompts(mcp: FastMCP) -> None:
    """Register BD-owned workflow guidance prompts."""
    prompts.registry.register_prompts(mcp)
