"""Adaptive LangGraph for book-scoped retrieval, synthesis, and verification."""
from __future__ import annotations
from . import entities, states
from .... import nodes

from langgraph.graph import END, START, StateGraph


def adaptive_ask_graph():
    """Compile the book-scoped planning, retrieval, evidence-gate, and answer workflow."""
    workflow = StateGraph(
        states.AdaptiveAskState,
        context_schema = entities.AskContext,
    )
    workflow.add_node("plan_research", nodes.ask.plan.node.plan_research)
    workflow.add_node("run_subagent", nodes.ask.retrieve.node.retrieve_evidence)
    workflow.add_node("grade_documents", nodes.ask.grade.node.grade_evidence)
    workflow.add_node("rewrite_query", nodes.ask.plan.node.rewrite_queries)
    workflow.add_node("generate", nodes.ask.answer.node.generate_answer)
    workflow.add_node("synthesize", nodes.ask.answer.node.generate_answer)
    workflow.add_node("critic", nodes.ask.critic.node.verify_answer)
    workflow.add_node("format_citations", nodes.ask.answer.node.format_citations)
    workflow.add_edge(START, "plan_research")
    workflow.add_edge("plan_research", "run_subagent")
    workflow.add_edge("run_subagent", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        nodes.ask.grade.node.after_grade,
        {
            "rewrite_query": "rewrite_query",
            "generate": "generate",
            "synthesize": "synthesize",
        },
    )
    workflow.add_edge("rewrite_query", "run_subagent")
    workflow.add_edge("generate", "critic")
    workflow.add_edge("synthesize", "critic")
    workflow.add_edge("critic", "format_citations")
    workflow.add_edge("format_citations", END)
    return workflow.compile()
