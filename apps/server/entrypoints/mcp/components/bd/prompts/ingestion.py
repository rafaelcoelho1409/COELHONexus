"""Guidance prompts for evidence-based Ingestion quality review."""
from __future__ import annotations

from ..tools.search import domain


def review_ingestion_run(book_id: str, agentic_run_id: str) -> str:
    """Guide an agent through a read-only review of one Agentic Ingestion run."""
    if not domain.safe_segment(book_id) or not domain.safe_segment(agentic_run_id):
        raise ValueError("book_id and agentic_run_id must be safe path segments")
    run_uri = f"bd://books/{book_id}/ingestion/runs/{agentic_run_id}"
    quality_uri = f"{run_uri}/quality"
    return f"""Review BD Agentic Ingestion run `{agentic_run_id}` for book `{book_id}`.

Read `{run_uri}` and `{quality_uri}` and use `bd_ingestion_get_run` plus `bd_ingestion_get_quality_summary` if needed.
Summarize run status, unfinished or failed stages, review and unverified page counts, and any quality limitation.
Separate observed manifest facts from your interpretation. Do not infer page-level content from aggregate counts.
Do not start, resume, cancel, or replay a run during this review."""
