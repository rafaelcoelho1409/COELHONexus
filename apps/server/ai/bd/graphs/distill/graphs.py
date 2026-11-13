"""The source-only Distill workflow: extract units, synthesize chapters and the book, then publish the report."""
from __future__ import annotations
import domains
from . import entities, states
from ... import nodes

import functools
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy


def distill_graph(
    dependencies: entities.DistillDependencies,
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
    workflow = StateGraph(states.DistillRunState)
    workflow.add_node(
        "prepare_units",
        functools.partial(
            nodes.distill.extract_learning_units.node.prepare_units, 
            dependencies),
    )
    workflow.add_node(
        "extract_unit",
        functools.partial(
            nodes.distill.extract_learning_units.node.extract_unit, 
            dependencies),
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "prepare_chapters",
        functools.partial(
            nodes.distill.synthesize.node.prepare_chapters, 
            dependencies),
    )
    workflow.add_node(
        "synthesize_chapter",
        functools.partial(
            nodes.distill.synthesize.node.synthesize_chapter, 
            dependencies),
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "synthesize_book",
        functools.partial(
            nodes.distill.synthesize.node.synthesize_book, 
            dependencies),
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "publish_report",
        functools.partial(
            nodes.distill.publish_report.node.publish_report, 
            dependencies),
    )
    workflow.add_conditional_edges(
        START,
        nodes.distill.extract_learning_units.node.after_start,
        ["prepare_units", "prepare_chapters"],
    )
    workflow.add_conditional_edges(
        "prepare_units",
        nodes.distill.extract_learning_units.node.dispatch_units,
        ["extract_unit", "prepare_chapters"],
    )
    workflow.add_edge("extract_unit", "prepare_chapters")
    workflow.add_conditional_edges(
        "prepare_chapters",
        nodes.distill.synthesize.node.dispatch_chapters,
        ["synthesize_chapter", "synthesize_book"],
    )
    workflow.add_edge("synthesize_chapter", "synthesize_book")
    workflow.add_edge("synthesize_book", "publish_report")
    workflow.add_edge("publish_report", END)
    return workflow.compile(checkpointer = checkpointer)
