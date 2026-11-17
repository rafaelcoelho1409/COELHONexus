"""Internal connection details shared by Search MCP integrations."""
from __future__ import annotations

from dataclasses import dataclass

from .params import ProviderId


@dataclass(frozen = True, slots = True)
class MCPConnection:
    provider_id: ProviderId
    endpoint: str
    headers: dict[str, str]
