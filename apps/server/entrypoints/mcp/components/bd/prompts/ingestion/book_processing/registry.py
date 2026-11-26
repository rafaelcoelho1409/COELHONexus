"""FastMCP registration boundary for BD Ingestion prompts."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register the read-only ingestion review prompt."""

    @mcp.prompt(
        name = "bd_review_ingestion_run",
        title = "Review BD Ingestion Run",
        description = "Review run progress and aggregate quality evidence without mutating the run.",
        tags = {"bd", "ingestion", "review"},
    )
    def review_ingestion_run(book_id: str, agentic_run_id: str) -> str:
        """Guide an agent through a read-only review of one Agentic Ingestion run.

        Args:
            book_id: BD book identifier whose ingestion run should be reviewed.
            agentic_run_id: Agentic Ingestion run identifier to review.
        """
        return domain.build_review_ingestion_run_prompt(book_id, agentic_run_id)
