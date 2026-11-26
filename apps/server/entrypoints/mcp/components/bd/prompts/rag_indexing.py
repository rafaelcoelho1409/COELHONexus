"""Guidance prompts for safe, read-only Agentic RAG index diagnosis."""
from __future__ import annotations

from ..tools.search import domain


def diagnose_index_run(book_id: str, agentic_run_id: str) -> str:
    """Guide an agent through a read-only diagnosis of one indexing run."""
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    run_uri = f"bd://books/{book_id}/rag/index-runs/{agentic_run_id}"
    current_uri = f"bd://books/{book_id}/rag/current-index"
    return f"""Diagnose BD Agentic RAG indexing run `{agentic_run_id}` for book `{book_id}`.

Read `{run_uri}` and `{current_uri}` and use `bd_rag_index_get_run` and `bd_rag_index_get_current` if needed.
Identify the current or failed graph stage, compare completed backend counts, and state whether a validated current index exists.
Recommend a replay node only when the run state and completed predecessors support it. Treat that as a recommendation for an operator;
do not call resume, cancel, or replay tools."""
