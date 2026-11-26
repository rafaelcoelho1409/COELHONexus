"""FastMCP server mounted into the COELHO Nexus ASGI process."""
from __future__ import annotations
from . import components

from fastmcp import FastMCP


mcp = FastMCP("COELHO Nexus")
components.registry.register_tools(mcp)
components.registry.register_resources(mcp)
components.registry.register_prompts(mcp)
