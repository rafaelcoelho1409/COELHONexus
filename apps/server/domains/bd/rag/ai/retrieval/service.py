"""Coordinate retrieval across the published BD indexes and fuse evidence."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import domains

from . import domain, params

logger = logging.getLogger(__name__)


async def _retrieve_one_source(
    source: str,
    book_id: str,
    query: str,
    index: dict[str, Any],
    top_k: int,
    neo4j_intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
) -> list[dict[str, Any]]:
    return await asyncio.wait_for(
        domains.bd.rag.source.retrieval.service.retrieve_source(
            source, book_id, query, index, top_k,
            neo4j_intent = neo4j_intent,
        ),
        timeout = params.RETRIEVAL_TIMEOUT_S,
    )


async def _read_index(book_id: str, run_id: str) -> dict[str, Any]:
    """Resolve the current published Ingestion index and verify its exact run."""
    if not book_id or not run_id:
        raise ValueError("book_id and run_id are required")
    ingestion_index = await domains.bd.rag.ai.runs.service.read_current_index(book_id)
    if ingestion_index is None:
        raise ValueError("The book has no completed Ingestion retrieval index")
    if ingestion_index.get("agentic_run_id") != run_id:
        raise ValueError("The selected run is not the current Ingestion retrieval index")
    return {**ingestion_index["index"], **ingestion_index}

async def retrieve_source(
    book_id: str,
    run_id: str,
    query: str,
    source: str,
    *,
    top_k: int | None = None,
    neo4j_intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
) -> dict[str, Any]:
    """Retrieve bounded, revision-pinned evidence from one BD RAG source.

    This is the shared source contract for the MCP tools and source-specific
    DeepAgents. Backend failures become explicit per-source statuses so a
    partial retrieval cannot be mistaken for a complete empty result.
    """
    query = query.strip()[:params.MAX_QUERY_CHARS]
    if not query:
        return {"status": "invalid_query", "source": source, "query": "", "evidence": []}
    if source not in {"elasticsearch", "qdrant", "neo4j"}:
        return {"status": "invalid_source", "source": source, "query": query, "evidence": []}
    if neo4j_intent is not None and source != "neo4j":
        return {"status": "invalid_query", "source": source, "query": query, "evidence": []}
    if top_k is None:
        top_k = domains.bd.rag.source.retrieval.params.TOP_K
    top_k = max(1, min(int(top_k), domains.bd.rag.source.retrieval.params.TOP_K))
    try:
        index = await _read_index(book_id, run_id)
        evidence = await _retrieve_one_source(
            source,
            book_id,
            query,
            index,
            top_k,
            neo4j_intent = neo4j_intent,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return {
            "status": "timeout", "source": source, "query": query,
            "evidence": [], "error_type": "TimeoutError",
        }
    except Exception as error:  # noqa: BLE001 — one source must not mask sibling evidence.
        return {
            "status": "unavailable", "source": source, "query": query,
            "evidence": [], "error_type": type(error).__name__,
        }
    return {
        "status": "complete" if evidence else "no_results",
        "source": source,
        "query": query,
        "evidence": evidence,
    }


async def retrieve_sources(
    book_id: str,
    run_id: str,
    query: str,
    *,
    top_k: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Query all local stores concurrently and retain each backend's status."""
    query = query.strip()[:params.MAX_QUERY_CHARS]
    if not query:
        return {
            source: {"status": "invalid_query", "source": source, "query": "", "evidence": []}
            for source in ("elasticsearch", "qdrant", "neo4j")
        }
    index = await _read_index(book_id, run_id)
    if top_k is None:
        top_k = domains.bd.rag.source.retrieval.params.TOP_K
    top_k = max(1, min(int(top_k), domains.bd.rag.source.retrieval.params.TOP_K))
    sources = ("elasticsearch", "qdrant", "neo4j")
    results = await asyncio.gather(*(
        asyncio.wait_for(
            _retrieve_one_source(source, book_id, query, index, top_k),
            timeout = params.RETRIEVAL_TIMEOUT_S,
        )
        for source in sources
    ), return_exceptions = True)
    output: dict[str, dict[str, Any]] = {}
    for source, result in zip(sources, results, strict = True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            output[source] = {
                "status": "timeout" if isinstance(result, TimeoutError) else "unavailable",
                "source": source,
                "query": query,
                "evidence": [],
                "error_type": type(result).__name__,
            }
        else:
            output[source] = {
                "status": "complete" if result else "no_results",
                "source": source,
                "query": query,
                "evidence": result,
            }
    return output


async def retrieve(book_id: str, run_id: str, query: str) -> list[dict[str, Any]]:
    """Retrieve and fuse evidence while preserving source failures in logs."""
    results = await retrieve_sources(book_id, run_id, query)
    unavailable = [
        f"{source}={result['status']}" for source, result in results.items()
        if result["status"] in {"unavailable", "timeout"}
    ]
    if unavailable:
        logger.warning(
            "BD retrieval had partial backend failures: %s", ", ".join(unavailable),
        )
    return fuse_evidence([result["evidence"] for result in results.values()])


def fuse_evidence(ranked_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Fuse backend or query rankings without comparing incomparable raw scores."""
    return domain.fuse_evidence(ranked_lists)
