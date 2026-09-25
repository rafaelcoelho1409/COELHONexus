"""Business domains. One sub-package per bounded context (rr, settings,
future ycs, future dd). Mirrors `apps/fastapi/domains/`.

The MCP surface (tools/resources/prompts) lives in the feature domains;
`settings` is the credential backing store with no MCP surface.
"""
from __future__ import annotations
from . import rr, settings


__all__ = ["rr", "settings"]
