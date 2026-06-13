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
"""
from fastmcp import FastMCP

from . import concept, latest_digest


def register(mcp: FastMCP) -> None:
    """Register all RR resources on the root server."""
    latest_digest.register(mcp)
    concept.register(mcp)
