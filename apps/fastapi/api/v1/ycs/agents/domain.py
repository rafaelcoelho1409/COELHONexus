"""ycs/agents domain — pure LangGraph update serializer (no I/O)."""
from __future__ import annotations

from typing import Any


def serialize_update(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
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
        # 2026-09-16: ship the FULL list (not just latest) so the
        # frontend flips every card even on a bulk `run_subagents`
        # return; `latest_*` stay for backward compat with older JS.
        result["sub_results"] = [
            {
                "sub_question": (item.get("sub_question", "") or ""),
                "answer":       (item.get("answer", "") or ""),
                "error_kind":   (item.get("error_kind", "") or ""),
            }
            for item in update["sub_results"]
            if isinstance(item, dict)
        ]
        latest = update["sub_results"][-1]
        result["latest_sub_question"] = latest.get("sub_question", "")
        result["latest_sub_answer"] = latest.get("answer", "")
        result["latest_sub_error_kind"] = latest.get("error_kind", "")
    if "confidence_score" in update and update["confidence_score"]:
        result["confidence_score"] = update["confidence_score"]
    return result
