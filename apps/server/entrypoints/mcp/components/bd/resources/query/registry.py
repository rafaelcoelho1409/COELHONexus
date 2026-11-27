"""Register BD Query catalog and schema resources."""
from __future__ import annotations
from . import resources

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    """Register the BD Query backend catalog and schema template."""
    handlers = resources.BDQueryResources()

    @mcp.resource(
        "bd://query/backends",
        name="bd_query_backends",
        title="BD Query Backends",
        description="Query formats, scope, defaults, and safe result limits for BD stores.",
        mime_type="application/json",
        tags={"bd", "query", "schema"},
    )
    def backends() -> dict:
        """Return the supported BD Query backend catalog."""
        return handlers.backends().model_dump(mode = "json")

    @mcp.resource(
        "bd://query/{backend}/schema",
        name="bd_query_schema",
        title="BD Query Backend Schema",
        description="Current BD mappings, up to 50 prioritized Qdrant embedding collections, or typed Neo4j graph schema.",
        mime_type="application/json",
        tags={"bd", "query", "schema"},
    )
    async def schema(backend: str) -> dict:
        """Read one backend schema and its fingerprint.

        Args:
            backend: One of elasticsearch, qdrant, or neo4j.
        """
        snapshot = await handlers.schema(backend)
        return snapshot.model_dump(mode = "json")
