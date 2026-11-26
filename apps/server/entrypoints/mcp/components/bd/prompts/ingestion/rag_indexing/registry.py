"""FastMCP registration boundary for BD RAG indexing prompts."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register the read-only RAG indexing diagnosis prompt."""

    @mcp.prompt(
        name = "bd_diagnose_rag_index_run",
        title = "Diagnose BD RAG Index Run",
        description = "Diagnose indexing stages and recommend a safe replay point without executing it.",
        tags = {"bd", "rag", "diagnostics"},
    )
    def diagnose_index_run(book_id: str, agentic_run_id: str) -> str:
        """Guide an agent through a safe, read-only diagnosis of one indexing run.

        Args:
            book_id: BD book identifier whose indexing run should be diagnosed.
            agentic_run_id: Agentic RAG indexing run identifier to diagnose.
        """
        return domain.build_diagnose_index_run_prompt(book_id, agentic_run_id)
