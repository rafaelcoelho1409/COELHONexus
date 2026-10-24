"""`ping` — confirms the MCP endpoint is up and tools are callable."""
from __future__ import annotations
from . import params

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def ping() -> dict[str, str]:
        """Liveness probe — confirms the MCP server is up and tools are callable."""
        return {"status": params.STATUS_OK}
