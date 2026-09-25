"""FastMCP middleware — `on_call_tool` (+ siblings) cross-cutting hooks.

This is a FastMCP-specific concept (its own top-level dir alongside
domains/, mirroring how apps/fasthtml has features/ + layout/). Each
sub-module is one cross-cutting concern that applies to every tool call.

Today:
  - ratelimit.py   per-tool min-interval gate (tools self-register their
                   interval via ratelimit.register(name, seconds))

Deliberately no telemetry middleware: observability (OpenTelemetry +
LangFuse) lives in apps/fastapi/; this stays a simple MCP server (see
server.py). Installed on the root server via `mcp.add_middleware(...)`.
"""
from __future__ import annotations
from . import ratelimit


__all__ = ["ratelimit"]
