"""Adaptive LangGraph for book-scoped retrieval, synthesis, and verification."""
from __future__ import annotations

import langgraph.graph

from ..... import bd


def _after_grade(state: dict) -> str:
    if (
        not state.get("retrieval_sufficient")
        and int(state.get("retry_count", 0)) < bd.graphs.ask.adaptive.params.MAX_RETRIEVAL_RETRIES
    ):
        return "rewrite_query"
    return "synthesize" if state.get("mode") == "deep" else "generate"


def build_adaptive_ask_graph():
    workflow = langgraph.graph.StateGraph(
        bd.graphs.ask.adaptive.states.AdaptiveAskState,
        context_schema = bd.graphs.ask.adaptive.entities.AskContext,
    )
    workflow.add_node("plan_research", bd.nodes.ask.plan.plan_research)
    workflow.add_node("run_subagent", bd.nodes.ask.retrieve.retrieve_evidence)
    workflow.add_node("grade_documents", bd.nodes.ask.grade.grade_evidence)
    workflow.add_node("rewrite_query", bd.nodes.ask.plan.rewrite_queries)
    workflow.add_node("generate", bd.nodes.ask.answer.generate_answer)
    workflow.add_node("synthesize", bd.nodes.ask.answer.generate_answer)
    workflow.add_node("critic", bd.nodes.ask.critic.verify_answer)
    workflow.add_node("format_citations", bd.nodes.ask.answer.format_citations)
    workflow.add_edge(langgraph.graph.START, "plan_research")
    workflow.add_edge("plan_research", "run_subagent")
    workflow.add_edge("run_subagent", "grade_documents")
    workflow.add_conditional_edges("grade_documents", _after_grade, {
        "rewrite_query": "rewrite_query",
        "generate": "generate",
        "synthesize": "synthesize",
    })
    workflow.add_edge("rewrite_query", "run_subagent")
    workflow.add_edge("generate", "critic")
    workflow.add_edge("synthesize", "critic")
    workflow.add_edge("critic", "format_citations")
    workflow.add_edge("format_citations", langgraph.graph.END)
    return workflow.compile()
