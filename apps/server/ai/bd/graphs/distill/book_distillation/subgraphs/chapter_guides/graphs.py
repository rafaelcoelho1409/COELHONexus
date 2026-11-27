"""Compile chapter preparation, parallel synthesis, and coverage review."""
from __future__ import annotations
import domains
from ... import entities, service, states
from ...... import nodes

from langgraph.graph import END, START, StateGraph


def chapter_guides_graph():
    """Compile the complete chapter guide production workflow."""
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    workflow.add_node(
        "prepare_chapters",
        nodes.distill.book_distillation.subgraphs.chapter_guides.prepare_chapters.node.prepare_chapters,
    )
    workflow.add_node(
        "synthesize_chapter",
        nodes.distill.book_distillation.subgraphs.chapter_guides.synthesize_chapter.node.synthesize_chapter,
        retry_policy = service.provider_retry_policy(
            retry_capacity_errors = False,
            max_attempts = domains.bd.distill.ai.params.CHAPTER_SYNTHESIS_NODE_RETRY_ATTEMPTS,
        ),
    )
    workflow.add_node(
        "review_chapter_coverage",
        nodes.distill.book_distillation.subgraphs.chapter_guides.review_chapter_coverage.node.review_chapter_coverage,
    )

    workflow.add_edge(START, "prepare_chapters")
    workflow.add_conditional_edges(
        "prepare_chapters",
        nodes.distill.book_distillation.subgraphs.chapter_guides.prepare_chapters.node.dispatch_chapters,
        ["synthesize_chapter"],
    )
    workflow.add_edge("synthesize_chapter", "review_chapter_coverage")
    workflow.add_edge("review_chapter_coverage", END)
    return workflow.compile()
