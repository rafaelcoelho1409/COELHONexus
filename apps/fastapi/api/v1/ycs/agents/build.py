"""ycs/agents — graph factory and LangGraph astream serializer."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from domains.ycs.rag.adaptive import build_adaptive_rag_graph


async def build_graph_from_request(request: Request):
    """Build the adaptive RAG graph wired to `app.state.llm` (rotator chain, not BYOK override).
    BYOK exclusive-override was dropped — routing one user key through the rotator's fallback
    defeats explicit provider choice; same workload as Planner/Synth so same rotator path."""
    app = request.app
    return build_adaptive_rag_graph(
        retriever    = app.state.smart_retriever,
        grader       = app.state.grader,
        llm          = app.state.llm,
        checkpointer = None,
        neo4j_graph  = app.state.neo4j_graph,
    )


def _serialize_update(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    """Project a LangGraph astream update patch into a JSON-safe dict for SSE. Documents are slugged; generations pass through."""
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
    # Without this, citations were dropped at serialisation and only appeared on page reload.
    if "citations" in update and update["citations"]:
        result["citations"] = update["citations"]
    if "search_query" in update:
        result["search_query"] = update["search_query"]
    if "retry_count" in update:
        result["retry_count"] = update["retry_count"]
    if "mode" in update:
        result["mode"] = update["mode"]
    if "sub_questions" in update and update["sub_questions"]:
        result["sub_questions"] = update["sub_questions"]
    if "research_plan" in update and update["research_plan"]:
        result["research_plan"] = update["research_plan"]
    if "sub_results" in update and update["sub_results"]:
        result["sub_results_count"] = len(update["sub_results"])
        latest = update["sub_results"][-1]
        result["latest_sub_question"] = latest.get("sub_question", "")
        result["latest_sub_answer"] = latest.get("answer", "")
        result["latest_sub_error_kind"] = latest.get("error_kind", "")
    if "confidence_score" in update and update["confidence_score"]:
        result["confidence_score"] = update["confidence_score"]
    return result
