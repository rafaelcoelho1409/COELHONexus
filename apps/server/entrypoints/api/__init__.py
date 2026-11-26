"""api — inbound HTTP layer. Edge settings shared by every API version
(CORS origins, MCP host allow-list); routers live under `api/v1/`."""
from __future__ import annotations
from . import domain, params, v1


__all__ = [
    "domain",
    "params",
    "v1",
]
