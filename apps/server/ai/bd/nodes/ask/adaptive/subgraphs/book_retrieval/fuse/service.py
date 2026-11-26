"""Fuse Ask's query and source rankings into cited book evidence."""
from __future__ import annotations
import domains
from .. import params

from typing import Any


def fuse_evidence(state: dict[str, Any]) -> dict[str, Any]:
    """Preserve per-query statuses and fuse evidence without raw-score comparison."""
    source_results = {
        source: state.get(f"{source}_results") or []
        for source in ("elasticsearch", "qdrant", "neo4j")
    }
    ranked_by_query: dict[str, list[list[dict[str, Any]]]] = {}
    all_ranked_lists: list[list[dict[str, Any]]] = []
    source_status_by_query: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for source, results in source_results.items():
        for result in results:
            query = str(result.get("query") or "")
            if not query:
                continue
            source_status_by_query.setdefault(query, {})[source] = str(
                result.get("status") or "unavailable"
            )
            evidence = result.get("evidence") or []
            ranked_by_query.setdefault(query, []).append(evidence)
            all_ranked_lists.append(evidence)
            error_type = result.get("error_type")
            if error_type:
                errors.append(f"{source}: {error_type}")

    query_order = list(dict.fromkeys(state.get("queries") or []))
    query_documents = {
        query: domains.bd.rag.ai.retrieval.service.fuse_evidence(
            ranked_by_query.get(query, [])
        )
        for query in query_order
    }
    documents = domains.bd.rag.ai.retrieval.service.fuse_evidence(all_ranked_lists)
    sub_results = [
        {
            "sub_question": query,
            "answer": "\n".join(
                item.get("text", "")[:300]
                for item in query_documents.get(query, [])[:2]
            ) or "No indexed evidence was returned.",
        }
        for query in query_order
    ]

    return {
        "documents": documents[:params.MAX_MERGED_EVIDENCE],
        "document_count": min(len(documents), params.MAX_MERGED_EVIDENCE),
        "retrieval_sources": sorted({str(item.get("source")) for item in documents if item.get("source")}),
        "retrieval_source_status": source_status_by_query,
        "retrieval_error": "; ".join(errors)[:500],
        "sub_results": sub_results if state.get("mode") == "deep" else [],
    }
