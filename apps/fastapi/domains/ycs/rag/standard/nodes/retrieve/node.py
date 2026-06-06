"""ycs/rag/standard/nodes/retrieve — RETRIEVE node.

Calls into `SmartRetriever.retrieve(query, channel_ids)` and records
which retrieval arms contributed (qdrant_hybrid / neo4j_graph /
elasticsearch). The `retrieval_sources` field feeds the SSE stream
+ the final response envelope.

Direct port of deprecated `graphs/youtube/rag.py:L48-67`."""
from __future__ import annotations

from ...state import YouTubeRAGState


async def retrieve(
    state: YouTubeRAGState,
    retriever,
    channel_ids: list[str] | None = None,
) -> dict:
    """Search for documents matching the query. `channel_ids` is passed
    via closure from the parent graph build to scope retrieval."""
    query = state.get("search_query") or state["question"]
    try:
        documents = await retriever.retrieve(query, channel_ids)
    except Exception:
        documents = []
    sources = list({
        doc.metadata.get("source", "unknown") for doc in documents
    })
    return {
        "documents":         documents,
        "retrieval_sources": sources,
    }
