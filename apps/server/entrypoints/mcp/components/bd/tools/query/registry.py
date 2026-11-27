"""FastMCP registration boundary for BD Query tools."""
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
    """Register bounded BD Query operations as model-callable tools."""
    handlers = tools.BDQueryTools()

    @mcp.tool(
        name="bd_query_get_catalog",
        description="List BD Query backends, query formats, data scope, and result limits.",
        annotations=_READ_ONLY,
        timeout=params.SCHEMA_TIMEOUT_S,
    )
    def get_catalog() -> schemas.QueryBackendCatalog:
        """Return the stable catalog of BD Query backends."""
        return handlers.get_catalog()

    @mcp.tool(
        name="bd_query_get_schema",
        description="Read the current schema for one BD Query backend, including its schema fingerprint.",
        annotations=_READ_ONLY,
        timeout=params.SCHEMA_TIMEOUT_S,
    )
    async def get_schema(backend: schemas.QueryBackend) -> schemas.QuerySchemaSnapshot:
        """Read live mappings, vector collections, or typed graph schema.

        Args:
            backend: Store whose current BD schema should be read.
        """
        return await handlers.get_schema(backend)

    @mcp.tool(
        name="bd_query_draft",
        description="Draft and validate one read-only BD query; this tool never executes the query.",
        annotations=_DRAFT,
        timeout=params.DRAFT_TIMEOUT_S,
    )
    async def draft_query(
        backend: schemas.QueryBackend,
        user_request: Annotated[
            str, Field(min_length = 1, max_length = params.MAX_PROMPT_CHARS)
        ],
        previous_query: Annotated[
            str, Field(max_length = params.MAX_QUERY_CHARS)
        ] = "",
    ) -> schemas.QueryDraft:
        """Draft a query body using the live backend schema and repair it once if needed.

        Args:
            backend: Target store for the query body.
            user_request: Data request to express as one query.
            previous_query: Optional query to refine or repair.
        """
        return await handlers.draft(backend, user_request, previous_query)

    @mcp.tool(
        name="bd_query_validate",
        description="Validate a BD query against read-only guards and the current backend schema without executing it.",
        annotations=_READ_ONLY,
        timeout=params.VALIDATE_TIMEOUT_S,
    )
    async def validate_query(
        backend: schemas.QueryBackend,
        query: Annotated[
            str, Field(min_length = 1, max_length = params.MAX_QUERY_CHARS)
        ],
    ) -> schemas.QueryValidation:
        """Check one query and return a fingerprint for the schema used.

        Args:
            backend: Store whose query syntax should be checked.
            query: Elasticsearch JSON body, Qdrant operation JSON, or read-only Cypher.
        """
        return await handlers.validate(backend, query)

    @mcp.tool(
        name="bd_query_run",
        description="Run one validated, read-only BD query and return bounded structured results.",
        annotations=_READ_ONLY,
        timeout=params.RUN_TIMEOUT_S,
    )
    async def run_query(
        backend: schemas.QueryBackend,
        query: Annotated[
            str, Field(min_length = 1, max_length = params.MAX_QUERY_CHARS)
        ],
    ) -> schemas.QueryRunResult:
        """Revalidate and execute one query against the BD retrieval stores.

        Args:
            backend: Store against which the query should run.
            query: Elasticsearch JSON body, Qdrant operation JSON, or read-only Cypher.
        """
        return await handlers.run(backend, query)
