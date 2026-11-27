"""Validation and rendering for BD Distill workflow and retrieval prompts."""
from __future__ import annotations
import domains
from . import prompts


def build_create_prompt(
    book_id: str,
    idempotency_key: str,
    request: str,
    ingestion_ai_run_id: str | None = None,
) -> str:
    if not domains.bd.identifiers.domain.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("idempotency_key must contain between 1 and 160 characters")
    if ingestion_ai_run_id is not None and not domains.bd.identifiers.domain.is_safe_segment(
        ingestion_ai_run_id,
    ):
        raise ValueError("ingestion_ai_run_id must be a safe path segment")
    request = request.strip()
    if not request or len(request) > 1000:
        raise ValueError("request must contain between 1 and 1000 characters")

    ingestion_selection = (
        f"Pin the run to completed Agentic Ingestion run `{ingestion_ai_run_id}`."
        if ingestion_ai_run_id
        else "Use the current completed Agentic Ingestion run."
    )
    operation_instructions = (
        "The user explicitly selected a prompt to create a Learning Guide. Call "
        "`bd_distill_start_run` once with the supplied `book_id` and `idempotency_key`. "
        f"{ingestion_selection} Reuse the same key if the start request must be retried. "
        "After it returns, use `bd_distill_get_run` to report progress. If the run is still "
        "queued or running, give the user the run ID and current stage; continue checking only "
        "when the client supports a bounded wait. Once status is `done`, read the report "
        "resource and answer the request from that report."
    )
    return prompts.PROMPT_TEMPLATE.format(
        book_id = book_id,
        request = request,
        operation_instructions = operation_instructions,
        run_resource_uri = f"bd://books/{book_id}/distill/runs/{{run_id}}",
        report_resource_uri = f"bd://books/{book_id}/distill/runs/{{run_id}}/report",
        retrieval_resource_uri = f"bd://books/{book_id}/distill/runs/{{run_id}}/retrieval",
    )


def build_review_prompt(book_id: str, run_id: str, question: str) -> str:
    if not domains.bd.identifiers.domain.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if not domains.bd.identifiers.domain.is_safe_segment(run_id):
        raise ValueError("run_id must be a safe path segment")
    question = question.strip()
    if not question or len(question) > 1000:
        raise ValueError("question must contain between 1 and 1000 characters")

    operation_instructions = (
        f"Inspect run `{run_id}` with `bd_distill_get_run` or its status resource. "
        "If it is not `done`, summarize its current state and do not start, resume, cancel, "
        "or replay it. If it is `done`, read the completed report resource and answer the "
        "user's question with the report's source references and review status. Read the "
        "retrieval evidence resource only when the user asks about retrieval provenance or "
        "source-specific results."
    )
    return prompts.PROMPT_TEMPLATE.format(
        book_id = book_id,
        request = question,
        operation_instructions = operation_instructions,
        run_resource_uri = f"bd://books/{book_id}/distill/runs/{run_id}",
        report_resource_uri = f"bd://books/{book_id}/distill/runs/{run_id}/report",
        retrieval_resource_uri = f"bd://books/{book_id}/distill/runs/{run_id}/retrieval",
    )


def build_retrieval_prompt(
    source: str,
    book_id: str,
    run_id: str,
    request: str,
) -> str:
    """Render a source-specific prompt for one selected published book run."""
    if source not in {"elasticsearch", "qdrant", "neo4j", "web_search"}:
        raise ValueError("source must name a supported BD Distill retrieval source")
    if not domains.bd.identifiers.domain.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if not domains.bd.identifiers.domain.is_safe_segment(run_id):
        raise ValueError("run_id must be a safe path segment")
    request = request.strip()
    if not request or len(request) > 1000:
        raise ValueError("request must contain between 1 and 1000 characters")

    source_details = {
        "elasticsearch": (
            "Elasticsearch lexical retrieval",
            "bd://distill/book-retrieval/elasticsearch/schema",
            "bd_search_elasticsearch",
        ),
        "qdrant": (
            "Qdrant hybrid semantic retrieval",
            "bd://distill/book-retrieval/qdrant/schema",
            "bd_search_qdrant",
        ),
        "neo4j": (
            "Neo4j graph retrieval",
            "bd://distill/book-retrieval/neo4j/schema",
            "bd_search_neo4j",
        ),
        "web_search": (
            "optional external web search",
            "bd://distill/book-retrieval/web-search/policy",
            "bd_search_web",
        ),
    }
    source_title, schema_uri, tool_name = source_details[source]
    return prompts.RETRIEVAL_PROMPT_TEMPLATE.format(
        source = source_title,
        schema_uri = schema_uri,
        tool_name = tool_name,
        book_id = book_id,
        run_id = run_id,
        request = request,
    )
