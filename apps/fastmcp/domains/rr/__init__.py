"""Research Radar — MCP capabilities (tools / resources / prompts) for the
`/research-radar` feature. The agent in apps/fastapi/domains/rr connects to
this sub-server over Streamable-HTTP.

See docs/RESEARCH-RADAR-DESIGN-2026-06-10.md for the full design.
"""
from __future__ import annotations
from . import prompts, resources, server, tools


__all__ = ["prompts", "resources", "server", "tools"]
