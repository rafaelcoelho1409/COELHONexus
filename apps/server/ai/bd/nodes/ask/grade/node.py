"""LangGraph node and route selection for BD Ask evidence grading."""
from __future__ import annotations
from . import service
from .... import graphs

from typing import Literal

from langchain.runtime import Runtime


async def grade_evidence(
    graph_state: graphs.ask.adaptive.states.AdaptiveAskState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    """Grade retrieved evidence under graph progress reporting."""
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "grade_documents",
        lambda: service.grade_evidence(graph_state, runtime),
    )


def after_grade(graph_state: graphs.ask.adaptive.states.AdaptiveAskState) -> Literal["rewrite_query", "generate", "synthesize"]:
    """Choose whether retrieval should retry or answer generation should start."""
    if (
        not graph_state.get("retrieval_sufficient")
        and int(graph_state.get("retry_count", 0)) < graphs.ask.adaptive.params.MAX_RETRIEVAL_RETRIES
    ):
        return "rewrite_query"
    return "synthesize" if graph_state.get("mode") == "deep" else "generate"
