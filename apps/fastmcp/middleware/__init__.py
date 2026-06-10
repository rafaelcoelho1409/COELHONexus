"""FastMCP middleware — `on_call_tool` (+ siblings) cross-cutting hooks.

This is a FastMCP-specific concept (its own top-level dir alongside infra/
and domains/, mirroring how apps/fasthtml has features/ + layout/). Each
sub-module is one cross-cutting concern that applies to every tool call.

Today:
  - telemetry.py   one OTel span per tool call (dual-exported to Alloy +
                   LangFuse via infra.otel)
  - ratelimit.py   per-tool min-interval gate (tools self-register their
                   interval via ratelimit.register(name, seconds))

Both are installed on the root server at module-import time in
apps/fastmcp/server.py via `mcp.add_middleware(...)`.
"""
