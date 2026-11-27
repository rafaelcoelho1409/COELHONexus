"""Register YCS Query catalog and live-schema resources."""
from __future__ import annotations
from . import resources

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    handlers = resources.YCSQueryResources()

    @mcp.resource(
        "ycs://query/backends",
        name="ycs_query_backends",
        title="YCS Query Backends",
        description="YCS Query formats, data scope, defaults, and safe execution limits.",
        mime_type="application/json",
        tags={"ycs", "query", "schema"},
    )
    def backends() -> dict:
        """Return YCS backend formats, data scope, and result limits."""
        return handlers.backends().model_dump(mode="json")

    @mcp.resource(
        "ycs://query/{backend}/schema",
        name="ycs_query_schema",
        title="YCS Query Backend Schema",
        description="Declared and live schema for a YCS Elasticsearch, Qdrant, or Neo4j backend.",
        mime_type="application/json",
        tags={"ycs", "query", "schema"},
    )
    async def schema(backend: str) -> dict:
        """Return the schema snapshot for one YCS backend.

        Args:
            backend: One of elasticsearch, qdrant, or neo4j.
        """
        return (await handlers.schema(backend)).model_dump(mode="json")
