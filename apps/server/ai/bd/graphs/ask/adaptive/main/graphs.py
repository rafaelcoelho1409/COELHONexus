"""Compile the root adaptive Ask workflow from its direct nodes."""
from __future__ import annotations
from .. import entities, states, subgraphs
from ..... import nodes

from langgraph.graph import END, START, StateGraph


def adaptive_ask_graph():
    """Compile the book-scoped planning, retrieval, and answer workflow."""
    workflow = StateGraph(
        states.AdaptiveAskState,
        context_schema = entities.AskContext,
    )
    workflow.add_node(
        "plan_research",
        nodes.ask.adaptive.main.plan_research.node.plan_research,
    )
    workflow.add_node(
        "direct_answer",
        nodes.ask.adaptive.main.direct_answer.node.direct_answer,
    )
    # Subgraph
    workflow.add_node(
        "retrieve_evidence",
        subgraphs.book_retrieval.graphs.book_retrieval_graph(),
    )
    workflow.add_node(
        "search_external",
        nodes.ask.adaptive.main.search_external.node.search_external_evidence,
    )
    workflow.add_node(
        "grade_documents",
        nodes.ask.adaptive.main.grade_documents.node.grade_evidence,
    )
    workflow.add_node(
        "rewrite_query",
        nodes.ask.adaptive.main.rewrite_query.node.rewrite_queries,
    )
    workflow.add_node(
        "generate",
        nodes.ask.adaptive.main.answer_generation.node.generate_answer,
    )
    workflow.add_node(
        "synthesize",
        nodes.ask.adaptive.main.answer_generation.node.generate_answer,
    )
    workflow.add_node(
        "critic",
        nodes.ask.adaptive.main.critic.node.verify_answer,
    )
    workflow.add_node(
        "format_citations",
        nodes.ask.adaptive.main.format_citations.node.format_citations,
    )

    workflow.add_edge(START, "plan_research")
    workflow.add_conditional_edges(
        "plan_research",
        nodes.ask.adaptive.main.plan_research.node.route_after_plan,
        {
            "conversation": "direct_answer",
            "book": "retrieve_evidence",
        },
    )
    workflow.add_edge("direct_answer", END)
    workflow.add_conditional_edges(
        "retrieve_evidence",
        nodes.ask.adaptive.main.search_external.node.route_after_book_retrieval,
        {
            "search": "search_external",
            "skip": "grade_documents",
        },
    )
    workflow.add_edge("search_external", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        nodes.ask.adaptive.main.grade_documents.node.after_grade,
        {
            "rewrite_query": "rewrite_query",
            "generate": "generate",
            "synthesize": "synthesize",
        },
    )
    workflow.add_edge("rewrite_query", "retrieve_evidence")
    workflow.add_edge("generate", "critic")
    workflow.add_edge("synthesize", "critic")
    workflow.add_edge("critic", "format_citations")
    workflow.add_edge("format_citations", END)
    return workflow.compile()
