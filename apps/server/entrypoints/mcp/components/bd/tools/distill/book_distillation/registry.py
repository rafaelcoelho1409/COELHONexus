"""FastMCP registration boundary for BD Distill tools."""
from __future__ import annotations
import domains
from . import params, schemas, tools

from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
)
_IDEMPOTENT_COMMAND = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
)
_COMMAND = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
_REPLAY = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
)
_OPEN_WORLD = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register_tools(mcp: FastMCP) -> None:
    """Register Distill lifecycle and book-retrieval tools."""
    handlers = tools.BDDistillTools()
    retrieval_handlers = tools.DistillBookRetrievalTools()

    @mcp.tool(
        name="bd_search_elasticsearch",
        description="Search the current published BD book index with bounded BM25 retrieval.",
        annotations=_READ_ONLY,
        timeout=params.RETRIEVAL_TIMEOUT_S,
    )
    async def search_elasticsearch(
        book_id: str, run_id: str, query: str
    ) -> schemas.RetrievalResult:
        """Search indexed book passages with BM25 retrieval.

        Args:
            book_id: BD book identifier whose published index should be searched.
            run_id: Agentic Ingestion run identifier that owns the indexed book.
            query: Search query for lexical retrieval.
        """
        return await retrieval_handlers.search_elasticsearch(book_id, run_id, query)

    @mcp.tool(
        name="bd_search_qdrant",
        description="Search the current published BD book index with dense and sparse hybrid retrieval.",
        annotations=_READ_ONLY,
        timeout=params.RETRIEVAL_TIMEOUT_S,
    )
    async def search_qdrant(
        book_id: str, run_id: str, query: str
    ) -> schemas.RetrievalResult:
        """Search indexed book passages with dense and sparse hybrid retrieval.

        Args:
            book_id: BD book identifier whose published index should be searched.
            run_id: Agentic Ingestion run identifier that owns the indexed book.
            query: Search query for hybrid retrieval.
        """
        return await retrieval_handlers.search_qdrant(book_id, run_id, query)

    @mcp.tool(
        name="bd_search_neo4j",
        description="Retrieve graph evidence from the current BD book using bounded entity and relationship hints.",
        annotations=_READ_ONLY,
        timeout=params.RETRIEVAL_TIMEOUT_S,
    )
    async def search_neo4j(
        book_id: str,
        run_id: str,
        query: str,
        intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
    ) -> schemas.RetrievalResult:
        """Retrieve entity and relationship evidence from the indexed book graph.

        Args:
            book_id: BD book identifier whose published graph should be searched.
            run_id: Agentic Ingestion run identifier that owns the indexed book.
            query: Search query for graph retrieval.
            intent: Optional bounded entity and relationship search hints.
        """
        return await retrieval_handlers.search_neo4j(book_id, run_id, query, intent)

    @mcp.tool(
        name="bd_search_web",
        description="Search the selected Settings Search MCP provider for bounded external context.",
        annotations=_OPEN_WORLD,
        timeout=params.WEB_SEARCH_TIMEOUT_S,
    )
    async def search_web(query: str) -> schemas.RetrievalResult:
        """Search external sources for context beyond the published BD index.

        Args:
            query: Short web search query for external context.
        """
        return await retrieval_handlers.search_web(query)

    @mcp.tool(
        name="bd_distill_get_run",
        description="Read BD Distill graph progress and run status without storage internals.",
        annotations=_READ_ONLY,
        timeout=params.READ_TIMEOUT_S,
    )
    async def get_run(book_id: str, run_id: str) -> schemas.DistillRunSummary:
        """Read status and progress for one BD Distill run.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: BD Distill run identifier to inspect.
        """
        return await handlers.get_run(book_id, run_id)

    @mcp.tool(
        name="bd_distill_start_run",
        description="Queue the complete BD Distill graph for a book and return its run ID.",
        annotations=_IDEMPOTENT_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def start_run(
        book_id: str,
        idempotency_key: Annotated[str, Field(min_length = 1, max_length = 160)],
        ingestion_ai_run_id: str | None = None,
    ) -> schemas.DistillCommandResult:
        """Queue a full Distill run; reuse the same key if retrying the request.

        Args:
            book_id: BD book identifier to distill.
            idempotency_key: Stable caller-generated key that prevents duplicate runs on retry.
            ingestion_ai_run_id: Optional completed Agentic Ingestion run to pin as input.
        """
        return await handlers.start_run(book_id, idempotency_key, ingestion_ai_run_id)

    @mcp.tool(
        name="bd_distill_resume_run",
        description="Resume a failed or cancelled BD Distill run from its saved graph checkpoint.",
        annotations=_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def resume_run(book_id: str, run_id: str) -> schemas.DistillCommandResult:
        """Resume a failed or cancelled Distill run from its saved checkpoint.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: Failed or cancelled Distill run to resume.
        """
        return await handlers.resume_run(book_id, run_id)

    @mcp.tool(
        name="bd_distill_cancel_run",
        description="Request cooperative cancellation of a queued or running BD Distill graph.",
        annotations=_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def cancel_run(book_id: str, run_id: str) -> schemas.DistillCommandResult:
        """Request cooperative cancellation of one Distill run.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: Queued or running Distill run to cancel.
        """
        return await handlers.cancel_run(book_id, run_id)

    @mcp.tool(
        name="bd_distill_replay_from_node",
        description="Replay a Distill graph node and descendants after explicit confirmation.",
        annotations=_REPLAY,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def replay_from_node(
        book_id: str,
        run_id: str,
        node_id: tools.DistillGraphNode,
        confirm_replay: bool,
    ) -> schemas.DistillCommandResult:
        """Replay a node and downstream stages; this can replace artifacts and incur model cost.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: Distill run whose downstream output should be replayed.
            node_id: Allowlisted graph stage from which to replay.
            confirm_replay: Must be true because replay can replace outputs and incur model cost.
        """
        return await handlers.replay_from_node(book_id, run_id, node_id, confirm_replay)
