"""Distill workflow with separate extraction, verification, synthesis, and publication stages."""
from __future__ import annotations
import domains
from . import entities, states
from ... import nodes

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy


def distill_graph(
    checkpointer: Any | None = None,
):
    """Compile the resumable Distill workflow with bounded retries for provider failures."""
    provider_retry_policy = RetryPolicy(
        max_attempts = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_ATTEMPTS,
        initial_interval = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S,
        backoff_factor = 2.0,
        max_interval = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_MAX_INTERVAL_S,
        jitter = True,
        retry_on = domains.settings.chat.domain.is_transient_provider_error,
    )
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    report_pipeline = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
    report_pipeline.add_node(
        "synthesize_book",
        nodes.distill.synthesize_book.node.synthesize_book_in_report_pipeline,
        retry_policy = provider_retry_policy,
    )
    report_pipeline.add_node(
        "research_context",
        nodes.distill.research_context.node.research_context,
        retry_policy = provider_retry_policy,
    )
    report_pipeline.add_node(
        "write_decision_brief",
        nodes.distill.write_decision_brief.node.write_decision_brief_in_report_pipeline,
        retry_policy = provider_retry_policy,
    )
    report_pipeline.add_conditional_edges(
        START,
        nodes.distill.synthesize_book.node.report_pipeline_entry,
        {
            "synthesize_book": "synthesize_book",
            "research_context": "research_context",
            "write_decision_brief": "write_decision_brief",
            "skip": END,
        },
    )
    report_pipeline.add_edge("synthesize_book", "research_context")
    report_pipeline.add_edge("research_context", "write_decision_brief")
    report_pipeline.add_edge("write_decision_brief", END)

    workflow.add_node(
        "prepare_units",
        nodes.distill.prepare_units.node.prepare_units,
    )
    workflow.add_node(
        "extract_unit",
        nodes.distill.extract_unit.node.extract_unit,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "verify_unit",
        nodes.distill.verify_unit.node.verify_unit,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "prepare_chapters",
        nodes.distill.prepare_chapters.node.prepare_chapters,
    )
    workflow.add_node(
        "synthesize_chapter",
        nodes.distill.synthesize_chapter.node.synthesize_chapter,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "review_chapter_coverage",
        nodes.distill.review_chapter_coverage.node.review_chapter_coverage,
    )
    workflow.add_node(
        domains.bd.distill.ai.params.PARALLEL_DISPATCH_NODE,
        nodes.distill.synthesize_chapter.node.prepare_parallel_stages,
    )
    workflow.add_node(
        "build_chapter_visuals",
        nodes.distill.build_chapter_visuals.node.build_chapter_visuals,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node("report_pipeline", report_pipeline.compile())
    workflow.add_node(
        "publish_report",
        nodes.distill.publish_report.node.publish_report,
    )
    workflow.add_conditional_edges(
        START,
        nodes.distill.prepare_units.node.after_start,
        ["prepare_units", "prepare_chapters", "publish_report"],
    )
    workflow.add_conditional_edges(
        "prepare_units",
        nodes.distill.prepare_units.node.dispatch_units,
        ["extract_unit", "prepare_chapters"],
    )
    workflow.add_conditional_edges(
        "extract_unit",
        nodes.distill.verify_unit.node.dispatch_units,
        ["verify_unit", "prepare_chapters"],
    )
    workflow.add_edge("verify_unit", "prepare_chapters")
    workflow.add_conditional_edges(
        "prepare_chapters",
        nodes.distill.prepare_chapters.node.dispatch_chapters,
        ["synthesize_chapter"],
    )
    # Fan-in all chapter synthesis Send tasks before running coverage review once.
    workflow.add_edge("synthesize_chapter", "review_chapter_coverage")
    workflow.add_edge("review_chapter_coverage", domains.bd.distill.ai.params.PARALLEL_DISPATCH_NODE)
    workflow.add_conditional_edges(
        domains.bd.distill.ai.params.PARALLEL_DISPATCH_NODE,
        nodes.distill.synthesize_chapter.node.dispatch_parallel_stages,
        ["build_chapter_visuals", "report_pipeline"],
    )
    workflow.add_edge(
        ["build_chapter_visuals", "report_pipeline"],
        "publish_report",
    )
    workflow.add_edge("publish_report", END)
    return workflow.compile(checkpointer = checkpointer)
