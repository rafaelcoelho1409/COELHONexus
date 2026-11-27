"""Register bounded YCS Query tools."""
from __future__ import annotations
from . import params, schemas, tools

from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_DRAFT = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


def register_tools(mcp: FastMCP) -> None:
    handlers = tools.YCSQueryTools()

    @mcp.tool(
        name="ycs_query_get_catalog",
        description="List YCS Query backends, query formats, data scope, and result limits.",
        annotations=_READ_ONLY,
        timeout=params.SCHEMA_TIMEOUT_S,
    )
    def get_catalog() -> schemas.QueryBackendCatalog:
        """Return YCS Query's supported stores and safe limits."""
        return handlers.get_catalog()

    @mcp.tool(
        name="ycs_query_get_schema",
        description="Read declared and live schema for one YCS Query backend.",
        annotations=_READ_ONLY,
        timeout=params.SCHEMA_TIMEOUT_S,
    )
    async def get_schema(backend: schemas.QueryBackend) -> schemas.QuerySchemaSnapshot:
        """Read live plus declared schema for one backend.

        Args:
            backend: Store whose YCS mappings, collection, or graph schema should be read.
        """
        return await handlers.get_schema(backend)

    @mcp.tool(
        name="ycs_query_draft",
        description="Draft and validate one read-only YCS query without executing it.",
        annotations=_DRAFT,
        timeout=params.DRAFT_TIMEOUT_S,
    )
    async def draft_query(
        backend: schemas.QueryBackend,
        user_request: Annotated[str, Field(min_length=1, max_length=params.MAX_PROMPT_CHARS)],
        previous_query: Annotated[str, Field(max_length=params.MAX_QUERY_CHARS)] = "",
    ) -> schemas.QueryDraft:
        """Draft and validate a YCS query without running it.

        Args:
            backend: Target store: elasticsearch, qdrant, or neo4j.
            user_request: Natural-language data request.
            previous_query: Optional body to refine or repair.
        """
        return await handlers.draft(backend, user_request, previous_query)

    @mcp.tool(
        name="ycs_query_validate",
        description="Validate YCS read-only guards, live schema references, and result caps without executing the query.",
        annotations=_READ_ONLY,
        timeout=params.VALIDATE_TIMEOUT_S,
    )
    async def validate_query(
        backend: schemas.QueryBackend,
        query: Annotated[str, Field(min_length=1, max_length=params.MAX_QUERY_CHARS)],
    ) -> schemas.QueryValidation:
        """Check read-only policy, schema references, and MCP result limits.

        Args:
            backend: Store whose query syntax should be checked.
            query: Elasticsearch JSON, Qdrant operation JSON, or read-only Cypher.
        """
        return await handlers.validate(backend, query)

    @mcp.tool(
        name="ycs_query_run",
        description="Revalidate and execute one bounded, read-only YCS query.",
        annotations=_READ_ONLY,
        timeout=params.RUN_TIMEOUT_S,
    )
    async def run_query(
        backend: schemas.QueryBackend,
        query: Annotated[str, Field(min_length=1, max_length=params.MAX_QUERY_CHARS)],
    ) -> schemas.QueryRunResult:
        """Revalidate and execute one bounded read-only query.

        Args:
            backend: Store against which the query should run.
            query: Elasticsearch JSON, Qdrant operation JSON, or read-only Cypher.
        """
        return await handlers.run(backend, query)
