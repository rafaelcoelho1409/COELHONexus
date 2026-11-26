"""FastMCP registration boundary for BD Agentic Ingestion tools."""
from __future__ import annotations
from . import params, schemas, tools

from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


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
    """Register typed BD Agentic Ingestion lifecycle tools."""
    handlers = tools.BDIngestionTools()

    @mcp.tool(
        name="bd_ingestion_get_run",
        description="Read a compact BD Agentic Ingestion run status and progress snapshot.",
        annotations=_READ_ONLY,
        timeout=params.READ_TIMEOUT_S,
    )
    async def get_run(book_id: str, agentic_run_id: str) -> schemas.IngestionRunSummary:
        """Read the status and graph progress for one Agentic Ingestion run.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Agentic Ingestion run identifier to read.
        """
        return await handlers.get_run(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_ingestion_get_quality_summary",
        description="Read aggregate quality counts for one BD Agentic Ingestion run.",
        annotations=_READ_ONLY,
        timeout=params.READ_TIMEOUT_S,
    )
    async def get_quality_summary(
        book_id: str, agentic_run_id: str
    ) -> schemas.IngestionQualitySummary:
        """Read aggregate page quality counts for one Ingestion run.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Agentic Ingestion run identifier to summarize.
        """
        return await handlers.get_quality_summary(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_ingestion_start_run",
        description="Queue BD Agentic Ingestion against a completed extraction and return its run ID.",
        annotations=_IDEMPOTENT_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def start_run(
        book_id: str,
        idempotency_key: Annotated[str, Field(min_length=1, max_length=160)],
        extraction_run_id: str | None = None,
    ) -> schemas.IngestionCommandResult:
        """Queue Ingestion for a completed extraction, reusing the key on retries.

        Args:
            book_id: BD book identifier to process.
            idempotency_key: Stable caller-generated key used to deduplicate retries.
            extraction_run_id: Completed extraction run to ingest, if explicitly selected.
        """
        return await handlers.start_run(book_id, idempotency_key, extraction_run_id)

    @mcp.tool(
        name="bd_ingestion_resume_run",
        description="Resume a failed or cancelled BD Agentic Ingestion run from its saved checkpoint.",
        annotations=_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def resume_run(
        book_id: str, agentic_run_id: str
    ) -> schemas.IngestionCommandResult:
        """Resume a failed or cancelled Ingestion run from its saved checkpoint.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Failed or cancelled Agentic Ingestion run to resume.
        """
        return await handlers.resume_run(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_ingestion_cancel_run",
        description="Request cooperative cancellation of a BD Agentic Ingestion run.",
        annotations=_COMMAND,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def cancel_run(
        book_id: str, agentic_run_id: str
    ) -> schemas.IngestionCommandResult:
        """Request cooperative cancellation of a queued or running Ingestion run.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Queued or running Agentic Ingestion run to cancel.
        """
        return await handlers.cancel_run(book_id, agentic_run_id)

    @mcp.tool(
        name="bd_ingestion_replay_from_node",
        description="Replay one allowlisted graph node and descendants after explicit confirmation.",
        annotations=_REPLAY,
        timeout=params.TOOL_TIMEOUT_S,
    )
    async def replay_from_node(
        book_id: str,
        agentic_run_id: str,
        node_id: tools.IngestionNode,
        confirm_replay: bool,
    ) -> schemas.IngestionCommandResult:
        """Replay an Ingestion graph node and descendants after confirmation.

        Args:
            book_id: BD book identifier that owns the Ingestion run.
            agentic_run_id: Agentic Ingestion run whose outputs should be replayed.
            node_id: Allowlisted graph node from which to replay.
            confirm_replay: Must be true because replay may replace outputs and incur model cost.
        """
        return await handlers.replay_from_node(
            book_id, agentic_run_id, node_id, confirm_replay
        )
