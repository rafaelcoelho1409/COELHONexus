"""Register YCS Query drafting and investigation prompts."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP


QueryBackend = Literal["elasticsearch", "qdrant", "neo4j"]


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="ycs_query_draft_query",
        title="Draft a YCS Query",
        description="Prepare a schema-aware, read-only YCS query without running it.",
        tags={"ycs", "query", "draft"},
    )
    def draft_query(
        backend: QueryBackend,
        user_request: Annotated[str, Field(min_length=1, max_length=4_000)],
        previous_query: Annotated[str, Field(max_length=100_000)] = "",
    ) -> str:
        """Prepare a request for drafting one YCS query."""
        return domain.build_draft_query_prompt(backend, user_request, previous_query)

    @mcp.prompt(
        name="ycs_query_investigate",
        title="Investigate YCS Data",
        description="Guide a bounded, read-only investigation of indexed YCS data.",
        tags={"ycs", "query", "investigate"},
    )
    def investigate(
        backend: QueryBackend,
        user_request: Annotated[str, Field(min_length=1, max_length=4_000)],
    ) -> str:
        """Prepare an end-to-end YCS Query investigation."""
        return domain.build_investigate_prompt(backend, user_request)
