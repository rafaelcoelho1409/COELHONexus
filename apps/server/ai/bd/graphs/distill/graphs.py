"""Distill workflow with separate extraction, verification, synthesis, and publication stages."""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

import domains

from ... import nodes
from . import entities, states


def distill_graph(
    checkpointer: Any | None = None,
):
    """Compile the resumable Distill workflow with bounded retries for provider failures."""
    provider_retry_policy = RetryPolicy(
        max_attempts = domains.bd.distill.params.TRANSIENT_NODE_RETRY_ATTEMPTS,
        initial_interval = domains.bd.distill.params.TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S,
        backoff_factor = 2.0,
        max_interval = domains.bd.distill.params.TRANSIENT_NODE_RETRY_MAX_INTERVAL_S,
        jitter = True,
        retry_on = domains.settings.chat.domain.is_transient_provider_error,
    )
    workflow = StateGraph(
        states.DistillRunState,
        context_schema = entities.DistillDependencies,
    )
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
        "build_chapter_visuals",
        nodes.distill.build_chapter_visuals.node.build_chapter_visuals,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "synthesize_book",
        nodes.distill.synthesize_book.node.synthesize_book,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "write_decision_brief",
        nodes.distill.write_decision_brief.node.write_decision_brief,
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "research_context",
        nodes.distill.research_context.node.research_context,
        retry_policy = provider_retry_policy,
    )
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
        ["synthesize_chapter", "synthesize_book"],
    )
    workflow.add_conditional_edges(
        "synthesize_chapter",
        nodes.distill.synthesize_chapter.node.dispatch_parallel_stages,
        ["build_chapter_visuals", "synthesize_book"],
    )
    workflow.add_edge("synthesize_book", "write_decision_brief")
    workflow.add_edge("synthesize_book", "research_context")
    workflow.add_edge(
        ["build_chapter_visuals", "write_decision_brief", "research_context"],
        "publish_report",
    )
    workflow.add_edge("publish_report", END)
    return workflow.compile(checkpointer = checkpointer)
