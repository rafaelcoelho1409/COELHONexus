"""Pure validation and rendering for BD Ingestion MCP prompts."""
from __future__ import annotations
import domains
from . import prompts


def build_review_ingestion_run_prompt(book_id: str, agentic_run_id: str) -> str:
    """Render the read-only review prompt for one validated ingestion run."""
    if not domains.bd.identifiers.domain.is_safe_segment(
        book_id
    ) or not domains.bd.identifiers.domain.is_safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    return prompts.PROMPT_TEMPLATE.format(
        book_id=book_id, agentic_run_id=agentic_run_id
    )
