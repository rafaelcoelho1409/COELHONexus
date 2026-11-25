"""Typed-state field names shared by BD RAG retrieval nodes."""

RETRIEVAL_RESULT_KEY_BY_SOURCE = {
    "elasticsearch": "elasticsearch_result",
    "qdrant": "qdrant_result",
    "neo4j": "neo4j_result",
    "web_search": "web_search_result",
}
