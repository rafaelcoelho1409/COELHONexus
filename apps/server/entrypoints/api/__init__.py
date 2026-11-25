"""api — inbound HTTP layer. Edge settings shared by every API version
(CORS origins, MCP host allow-list); routers live under `api/v1/`."""
from __future__ import annotations
from . import domain, middleware, params, v1


__all__ = [
    "domain",
    "middleware",
    "params",
    "v1",
]
