"""FastMCP registration boundary for BD Agentic RAG indexing tools."""
from __future__ import annotations
from . import params, schemas, tools

from fastmcp import FastMCP
from mcp.types import ToolAnnotations


_READ_ONLY = ToolAnnotations(
    read_only_hint=True, 
    destructive_hint=False, 
    idempotent_hint=True
)
_IDEMPOTENT_COMMAND = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
)
_COMMAND = ToolAnnotations(
    read_only_hint=False, 
    destructive_hint=False, 
    idempotent_hint=False
)
_REPLAY = ToolAnnotations(
    read_only_hint=False, 
    destructive_hint=True, 
    idempotent_hint=False
)


def register_tools(mcp: FastMCP) -> None:
    """Register typed BD Agentic RAG indexing lifecycle tools."""
    handlers = tools.BDRetrievalIndexTools()

    @mcp.tool(
        name="bd_rag_index_get_run",
        description="Read durable progress and store counts for one BD Agentic RAG index run.",
        annotations=_READ_ONLY,
        timeout=params.READ_TIMEOUT_S,
    )
    async def get_run(
        book_id: str, agentic_run_id: str
    ) -> schemas.RetrievalIndexSummary:
        """Read durable progress and backend counts for one indexing run.

        Args:
            book_id: BD book identifier that owns the indexing run.
            agentic_run_id: Agentic RAG indexing run identifier to read.
        """
        return await handlers.get_run(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_rag_index_get_current",
        description="Read the published BD index summary currently used by retrieval.",
        annotations=_READ_ONLY,
        timeout=params.READ_TIMEOUT_S,
    )
    async def get_current_index(book_id: str) -> schemas.CurrentIndexSummary:
        """Read the published retrieval index currently used for one book.

        Args:
            book_id: BD book identifier whose current index should be read.
        """
        return await handlers.get_current_index(book_id)

    @mcp.tool(
        name="bd_rag_index_start",
        description="Queue indexing for a completed BD Agentic Ingestion run.",
        annotations=_IDEMPOTENT_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def start_index(
        book_id: str, agentic_run_id: str
    ) -> schemas.IndexCommandResult:
        """Queue indexing for a completed Ingestion run.

        Args:
            book_id: BD book identifier that owns the completed Ingestion run.
            agentic_run_id: Completed Agentic Ingestion run to index.
        """
        return await handlers.start_index(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_rag_index_resume",
        description="Resume a failed, cancelled, or stale BD Agentic RAG index run.",
        annotations=_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def resume_index(
        book_id: str, agentic_run_id: str
    ) -> schemas.IndexCommandResult:
        """Resume a failed, cancelled, or stale indexing run.

        Args:
            book_id: BD book identifier that owns the indexing run.
            agentic_run_id: Agentic RAG indexing run to resume.
        """
        return await handlers.resume_index(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_rag_index_cancel",
        description="Request cooperative cancellation of a BD Agentic RAG index run.",
        annotations=_IDEMPOTENT_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def cancel_index(
        book_id: str, agentic_run_id: str
    ) -> schemas.IndexCommandResult:
        """Request cooperative cancellation of a queued or running indexing run.

        Args:
            book_id: BD book identifier that owns the indexing run.
            agentic_run_id: Queued or running Agentic RAG indexing run to cancel.
        """
        return await handlers.cancel_index(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_rag_index_replay_from_node",
        description="Replay one allowlisted index node and downstream stores after confirmation.",
        annotations=_REPLAY,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def replay_from_node(
        book_id: str,
        agentic_run_id: str,
        node_id: tools.IndexNode,
        confirm_replay: bool,
    ) -> schemas.IndexCommandResult:
        """Replay an index graph node and downstream stores after confirmation.

        Args:
            book_id: BD book identifier that owns the indexing run.
            agentic_run_id: Agentic RAG indexing run whose outputs should be replayed.
            node_id: Allowlisted index node from which to replay.
            confirm_replay: Must be true because replay may replace index outputs.
        """
        return await handlers.replay_from_node(
            book_id, agentic_run_id, node_id, confirm_replay
        )
