"""Register FastMCP capabilities owned by the BD domain."""
from __future__ import annotations

from fastmcp import FastMCP

from . import prompts, resources, tools


def register_tools(mcp: FastMCP, *, trusted_in_process: bool = False) -> None:
    """Register BD-owned tool features."""
    tools.search.registry.register_tools(mcp, trusted_in_process = trusted_in_process)
    tools.ingestion.registry.register_tools(mcp, trusted_in_process = trusted_in_process)
    tools.rag_indexing.registry.register_tools(mcp, trusted_in_process = trusted_in_process)


def register_resources(mcp: FastMCP, *, trusted_in_process: bool = False) -> None:
    """Register BD-owned run and index resources."""
    resources.registry.register_resources(mcp, trusted_in_process = trusted_in_process)


def register_prompts(mcp: FastMCP) -> None:
    """Register BD-owned workflow guidance prompts."""
    prompts.registry.register_prompts(mcp)
