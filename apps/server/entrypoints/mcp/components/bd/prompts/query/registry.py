"""Register user-selectable BD Query prompts."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP


QueryBackend = Literal["elasticsearch", "qdrant", "neo4j"]


def register_prompts(mcp: FastMCP) -> None:
    """Register draft-only and read-only investigation workflows."""

    @mcp.prompt(
        name="bd_query_draft_query",
        title="Draft a BD Query",
        description="Draft and validate a query without executing it.",
        tags={"bd", "query", "draft"},
    )
    def draft_query(
        backend: QueryBackend,
        user_request: Annotated[str, Field(min_length = 1, max_length = 4_000)],
        previous_query: Annotated[str, Field(max_length = 100_000)] = "",
    ) -> str:
        """Prepare a schema-aware BD query drafting request.

        Args:
            backend: Target backend: elasticsearch, qdrant, or neo4j.
            user_request: Data request to express as one query.
            previous_query: Optional query body to refine.
        """
        return domain.build_draft_query_prompt(backend, user_request, previous_query)

    @mcp.prompt(
        name="bd_query_investigate",
        title="Investigate BD Data",
        description="Draft, validate, and run a bounded read-only query against BD data.",
        tags={"bd", "query", "investigate"},
    )
    def investigate(
        backend: QueryBackend,
        user_request: Annotated[str, Field(min_length = 1, max_length = 4_000)],
    ) -> str:
        """Prepare a schema-aware BD data investigation request.

        Args:
            backend: Target backend: elasticsearch, qdrant, or neo4j.
            user_request: Question to investigate against indexed BD data.
        """
        return domain.build_investigate_prompt(backend, user_request)
