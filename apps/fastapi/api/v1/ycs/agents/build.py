"""ycs/agents — graph factory + streaming-update serializer.

Direct port of deprecated `routers/v1/youtube/helpers.py:L2014-L2108`.

`build_graph_from_request(request)` reads dependencies from `app.state`
(provisioned in lifespan) and returns a fresh compiled
`AdaptiveRAGGraph`. Deprecated rebuilt the graph on every request — we
follow that pattern; the graph compile is cheap (~ms) compared to LLM
calls.

`_serialize_update(node, update)` projects a LangGraph `astream` update
into a JSON-safe dict for SSE delivery."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from domains.ycs.rag.adaptive import build_adaptive_rag_graph


def build_graph_from_request(request: Request):
    """Build the adaptive RAG graph from FastAPI `app.state`.

    All deps are provisioned in `app.py` lifespan:
      - `smart_retriever` — ES + Qdrant + Neo4j fan-out + FlashRank
      - `grader`          — DocumentGrader bound to the LLM chain
      - `llm`             — 13-model `with_fallbacks` chain
      - `neo4j_graph`     — LangChain Neo4jGraph wrapper

    `checkpointer=None` matches deprecated — the adaptive parent graph
    intentionally does not checkpoint sub-agent runs (only the standard
    sub-graph would; we keep it stateless)."""
    app = request.app
    return build_adaptive_rag_graph(
        retriever    = app.state.smart_retriever,
        grader       = app.state.grader,
        llm          = app.state.llm,
        checkpointer = None,
        neo4j_graph  = app.state.neo4j_graph,
    )


def _serialize_update(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    """Project a LangGraph `astream(stream_mode='updates')` patch into a
    JSON-safe dict for SSE delivery.

    Direct port of deprecated `helpers.py:L2073-2108`. Document objects
    are slugged (video_id + title + preview); long generations pass through
    as-is."""
    result: dict[str, Any] = {"node": node_name}
    if "documents" in update:
        documents = update["documents"] or []
        result["documents"] = [
            {
                "video_id":        doc.metadata.get("video_id"),
                "title":           doc.metadata.get("title"),
                "source":          doc.metadata.get("source"),
                "content_preview": doc.page_content[:200],
            }
            for doc in documents
        ]
        result["document_count"] = len(documents)
    if "generation" in update:
        result["generation"] = update["generation"]
    if "search_query" in update:
        result["search_query"] = update["search_query"]
    if "retry_count" in update:
        result["retry_count"] = update["retry_count"]
    # Adaptive RAG fields
    if "mode" in update:
        result["mode"] = update["mode"]
    if "sub_questions" in update and update["sub_questions"]:
        result["sub_questions"] = update["sub_questions"]
    if "research_plan" in update and update["research_plan"]:
        result["research_plan"] = update["research_plan"]
    if "sub_results" in update and update["sub_results"]:
        result["sub_results_count"] = len(update["sub_results"])
        latest = update["sub_results"][-1]
        result["latest_sub_question"]      = latest.get("sub_question", "")
        result["latest_sub_answer_preview"] = latest.get("answer", "")[:200]
    if "confidence_score" in update and update["confidence_score"]:
        result["confidence_score"] = update["confidence_score"]
    return result
