"""Compile book synthesis, external context, and decision brief stages."""
from __future__ import annotations
from ... import entities, service, states
from ...... import nodes

from langgraph.graph import END, START, StateGraph


def _route_start(state: states.DistillRunState) -> str:
    """Enter at the requested report stage when replaying a leaf node."""
    replay_target = state.get("replay_target_node")
    if replay_target in {"synthesize_book", "research_context", "write_decision_brief"}:
        return replay_target
    if replay_target == "build_chapter_visuals":
        return "write_decision_brief"
    return "synthesize_book"


def book_report_graph():
    """Compile the report stages as one resumable subgraph."""
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    workflow.add_node(
        "synthesize_book",
        nodes.distill.book_distillation.subgraphs.book_report.synthesize_book.node.synthesize_book,
        retry_policy = service.provider_retry_policy(),
    )
    workflow.add_node(
        "research_context",
        nodes.distill.book_distillation.subgraphs.book_report.research_context.node.research_context,
        retry_policy = service.provider_retry_policy(),
    )
    workflow.add_node(
        "write_decision_brief",
        nodes.distill.book_distillation.subgraphs.book_report.write_decision_brief.node.write_decision_brief,
        retry_policy = service.provider_retry_policy(),
    )

    workflow.add_conditional_edges(
        START,
        _route_start,
        ["synthesize_book", "research_context", "write_decision_brief"],
    )
    workflow.add_edge("synthesize_book", "research_context")
    workflow.add_edge("research_context", "write_decision_brief")
    workflow.add_edge("write_decision_brief", END)
    return workflow.compile()
