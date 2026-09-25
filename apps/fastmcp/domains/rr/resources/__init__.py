"""FastMCP Resources for Research Radar.

Resources let an MCP client (the DeepAgents orchestrator, an external
Inspector, a third-party MCP-aware app) load CONTEXT without paying a
tool-call's cost — they're idempotent reads of named entities.

We expose two:

  radar://latest_digest          → the most recent persisted digest as JSON.
                                     Lets an agent restart-resume context
                                     without re-running a scan.
  radar://concept/{name}         → the Neo4j sub-graph for a named concept
                                     + its top related papers. Lets a deep_read
                                     or synthesis subagent pull "what else is
                                     related to X" without writing Cypher.

Registered via `register(mcp)` from this package's __init__.py — called
by apps/fastmcp/domains/rr/server.py.

Layout (per docs/CODE-CONVENTIONS.md §4):
  keys.py      URI registry (LATEST_DIGEST_URI, CONCEPT_URI_TEMPLATE)
  params.py    loose tunables (store timeouts, name cap)
  domain.py    PURE envelopes + concept-name validation
  service.py   I/O: Postgres scan lookup, MinIO digest load, Neo4j query
  resource.py  thin `@mcp.resource` boundary (one wrapper per resource)
  __init__.py  ← THIS — re-exports + register(mcp) dispatch only
"""
from __future__ import annotations
from . import domain, keys, params, resource, service

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


__all__ = ["domain", "keys", "params", "resource", "service"]


def register(mcp: FastMCP) -> None:
    """Register all RR resources on the root server."""
    resource.register(mcp)
