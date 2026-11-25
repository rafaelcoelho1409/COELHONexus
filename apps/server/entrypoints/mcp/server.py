"""FastMCP server instances mounted into the COELHO Nexus ASGI process."""
from __future__ import annotations

from . import deployment


mcp_profile = deployment.params.MCP_PROFILE
mcp = deployment.service.build_external_server()
internal_mcp = deployment.service.build_internal_server()
