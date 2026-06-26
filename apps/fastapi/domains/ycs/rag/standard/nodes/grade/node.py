"""ycs/rag/standard/nodes/grade — GRADE node.

Calls into the deprecated `DocumentGrader` (per-doc parallel
`asyncio.gather` of structured-output LLM calls).
"""
from __future__ import annotations

from domains.ycs.grader import DocumentGrader
from domains.ycs.runtime.observability import record_graded_docs, traced

from ...state import YouTubeRAGState


@traced("rag.grade")
async def grade_documents(
    state: YouTubeRAGState, grader: DocumentGrader,
) -> dict:
    """LLM evaluates each document for relevance IN PARALLEL."""
    relevant_docs = await grader.grade_documents(
        state["question"], state["documents"],
    )
    record_graded_docs(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
        count = len(relevant_docs),
    )
    return {"documents": relevant_docs}
