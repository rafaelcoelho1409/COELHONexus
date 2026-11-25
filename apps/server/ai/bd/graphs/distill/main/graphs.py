"""Compile the root Book Distiller workflow from direct nodes and subgraphs."""
from __future__ import annotations
import domains
from .. import entities, service, states, subgraphs
from .... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph


def _route_start(state: states.DistillRunState) -> str:
    """Route resumed runs to the owning root stage or its containing subgraph."""
    next_node = nodes.distill.subgraphs.unit_extraction.prepare_units.node.after_start(state)
    return {
        "prepare_units": "unit_extraction",
        "prepare_chapters": "chapter_guides",
    }.get(next_node, next_node)


def distill_graph(checkpointer: Any | None = None):
    """Compile the resumable Distill workflow with bounded provider retries."""
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    provider_retry_policy = service.provider_retry_policy()

    # Subgraph
    workflow.add_node("unit_extraction", subgraphs.unit_extraction.graphs.unit_extraction_graph())
    # Subgraph
    workflow.add_node("chapter_guides", subgraphs.chapter_guides.graphs.chapter_guides_graph())
    workflow.add_node(
        domains.bd.distill.ai.params.PARALLEL_DISPATCH_NODE,
        nodes.distill.main.dispatch_parallel_stages.node.prepare_parallel_stages,
    )
    workflow.add_node(
        "build_chapter_visuals",
        nodes.distill.main.build_chapter_visuals.node.build_chapter_visuals,
        retry_policy = provider_retry_policy,
    )
    # Subgraph
    workflow.add_node("book_retrieval", subgraphs.book_retrieval.graphs.book_retrieval_graph())
    workflow.add_node(
        "synthesize_book",
        nodes.distill.main.synthesize_book.node.synthesize_book,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "research_context",
        nodes.distill.main.research_context.node.research_context,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "write_decision_brief",
        nodes.distill.main.write_decision_brief.node.write_decision_brief,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "publish_report",
        nodes.distill.main.publish_report.node.publish_report,
    )

    workflow.add_conditional_edges(
        START,
        _route_start,
        ["unit_extraction", "chapter_guides", "publish_report"],
    )
    workflow.add_edge("unit_extraction", "chapter_guides")
    workflow.add_edge("chapter_guides", domains.bd.distill.ai.params.PARALLEL_DISPATCH_NODE)
    workflow.add_conditional_edges(
        domains.bd.distill.ai.params.PARALLEL_DISPATCH_NODE,
        nodes.distill.main.dispatch_parallel_stages.node.dispatch_parallel_stages,
        [
            "build_chapter_visuals",
            "book_retrieval",
            "synthesize_book",
            "research_context",
            "write_decision_brief",
        ],
    )
    workflow.add_edge("book_retrieval", "synthesize_book")
    workflow.add_edge("synthesize_book", "research_context")
    workflow.add_edge("research_context", "write_decision_brief")
    workflow.add_edge(
        ["build_chapter_visuals", "write_decision_brief"],
        "publish_report",
    )
    workflow.add_edge("publish_report", END)
    return workflow.compile(checkpointer = checkpointer)
