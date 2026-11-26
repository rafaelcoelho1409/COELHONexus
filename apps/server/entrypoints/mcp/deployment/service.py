"""Compose external and trusted internal FastMCP server profiles."""
from __future__ import annotations

from fastmcp import FastMCP

from .. import authentication, components
from . import domain, params


def build_external_server() -> FastMCP:
    """Build the HTTP-facing server according to the configured MCP profile."""
    _validate_profile()
    mcp = FastMCP(
        "COELHO Nexus",
        auth = authentication.service.build_auth_provider(params.MCP_PROFILE),
    )
    include_bd = params.MCP_PROFILE in {"local_demo", "protected"}
    components.registry.register_tools(mcp, include_bd = include_bd)
    components.registry.register_resources(mcp, include_bd = include_bd)
    components.registry.register_prompts(mcp, include_bd = include_bd)
    return mcp


def build_internal_server() -> FastMCP:
    """Build the private in-process MCP server for trusted BD agent clients."""
    _validate_profile()
    mcp = FastMCP("COELHO Nexus BD (internal)")
    components.registry.register_tools(mcp, trusted_in_process = True)
    components.registry.register_resources(mcp, trusted_in_process = True)
    components.registry.register_prompts(mcp)
    return mcp


def _validate_profile() -> None:
    domain.validate_profile(
        params.MCP_PROFILE,
        params.ENVIRONMENT,
        components.bd.tools.search.params.DEMO_BOOK_IDS,
        components.bd.tools.search.params.WEB_SEARCH_ENABLED,
    )
