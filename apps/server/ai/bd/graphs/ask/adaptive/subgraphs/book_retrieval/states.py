"""Private state contract for Ask's indexed-book retrieval subgraph."""
from __future__ import annotations

from typing import TypedDict


class BookRetrievalState(TypedDict, total = False):
    question: str
    thread_id: str
    turn_id: str
    queries: list[str]
    mode: str
    use_external_search: bool
    external_search_query: str
    external_search_status: str
    external_search_provider: str
    external_search_error: str
    external_documents: list[dict]
    web_search_result: dict
    elasticsearch_results: list[dict]
    qdrant_results: list[dict]
    neo4j_results: list[dict]
    documents: list[dict]
    document_count: int
    retrieval_sources: list[str]
    retrieval_source_status: dict[str, dict[str, str]]
    retrieval_error: str
    sub_results: list[dict]
