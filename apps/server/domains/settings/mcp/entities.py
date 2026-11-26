"""Connection details for a remote MCP server."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen = True, slots = True)
class MCPConnection:
    provider_id: str
    endpoint: str
    headers: dict[str, str] = field(default_factory = dict)
