"""Resource: `radar://concept/{name}`

Returns the Neo4j subgraph for one named concept — the concept node
itself, plus the papers tagged with `:ABOUT->Concept{name}`, plus a
shallow neighborhood (immediate co-occurring concepts).

Synthesis subagent uses this for "what else is in this theme's cluster?"
without writing Cypher. Future FastHTML "concept browser" affordance
reads from the same URI.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastmcp import FastMCP
from neo4j import AsyncGraphDatabase


logger = logging.getLogger(__name__)


def _neo4j_driver():
    """Open a one-shot Neo4j driver for this request. The MCP server is
    short-lived and we don't share connection pools at the resource layer."""
    uri  = os.environ["NEO4J_URI"]
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    pwd  = os.environ.get("NEO4J_PASSWORD", "")
    auth = (user, pwd) if pwd else None
    return AsyncGraphDatabase.driver(uri, auth=auth)


_CYPHER = """
MATCH (c:Concept {name: $name})
OPTIONAL MATCH (c)<-[:ABOUT]-(p:Paper)
WITH c, collect(DISTINCT {
    arxiv_id: p.id, title: p.title, signal: p.signal,
    citations: p.citations, published: p.published
}) AS papers
OPTIONAL MATCH (c)<-[:ABOUT]-(:Paper)-[:ABOUT]->(c2:Concept)
WHERE c2.name <> $name
WITH c, papers, collect(DISTINCT c2.name)[..15] AS related_concepts
RETURN {
    name: c.name,
    family: coalesce(c.family, ''),
    papers: papers[..20],
    related_concepts: related_concepts
} AS payload
"""


def register(mcp: FastMCP) -> None:
    """Register `radar://concept/{name}` on the root server."""

    @mcp.resource("radar://concept/{name}")
    async def concept(name: str) -> str:
        """Return a JSON blob with this concept's papers + related concepts.

        Args:
            name: The concept name as stored in Neo4j (e.g. 'constrained_decoding').
                  Case-sensitive — Neo4j MERGE was case-preserving.
        """
        if not name or len(name) > 200:
            return json.dumps({"error": "name must be 1-200 chars"})
        driver = _neo4j_driver()
        try:
            async with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as s:
                result = await s.run(_CYPHER, {"name": name})
                record = await result.single()
        except Exception as e:
            logger.warning(f"[rr-resource:concept] {name!r} query failed: {e}")
            return json.dumps({"error": f"{type(e).__name__}: {str(e)[:200]}"})
        finally:
            await driver.close()
        if record is None or record["payload"] is None:
            return json.dumps({
                "error": "concept not found in Neo4j",
                "name":  name,
                "hint":  "Run a scan that mentions this concept first.",
            })
        return json.dumps(record["payload"], default=str)
