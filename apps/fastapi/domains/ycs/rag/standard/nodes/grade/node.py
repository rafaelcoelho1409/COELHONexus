"""ycs/rag/standard/nodes/grade — GRADE node.

Calls into the deprecated `DocumentGrader` (per-doc parallel
`asyncio.gather` of structured-output LLM calls).
"""
from __future__ import annotations

import logging
import os

from domains.ycs.grader import DocumentGrader
from domains.ycs.runtime.observability import record_graded_docs, traced

from ...state import YouTubeRAGState


logger = logging.getLogger(__name__)


@traced("rag.grade")
async def grade_documents(
    state: YouTubeRAGState, grader: DocumentGrader,
) -> dict:
    """LLM evaluates each document for relevance IN PARALLEL.

    2026-09-15 ablation gate: `YCS_ABLATE_NO_GRADE=1` passes reranked
    documents straight through (order preserved) so grader ON/OFF can
    be A/B-measured without a redeploy per arm (`kubectl set env`
    flips it with a fast restart). Default off — production path
    unchanged."""
    if os.getenv("YCS_ABLATE_NO_GRADE") == "1":
        docs = list(state["documents"] or [])
        logger.warning(
            f"[ycs:ablation] grader BYPASSED — passing {len(docs)} "
            f"reranked doc(s) straight to generate"
        )
        record_graded_docs(
            route = str(state.get("route") or "unknown"),
            mode = str(state.get("mode") or "standard") + "+no-grade",
            count = len(docs),
        )
        return {"documents": docs}
    relevant_docs = await grader.grade_documents(
        state["question"], state["documents"],
    )
    record_graded_docs(
        route = str(state.get("route") or "unknown"),
        mode = str(state.get("mode") or "standard"),
        count = len(relevant_docs),
    )
    return {"documents": relevant_docs}
