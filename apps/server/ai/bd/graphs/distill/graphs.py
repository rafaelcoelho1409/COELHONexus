"""Build the source-only, resumable Distill workflow."""
from __future__ import annotations

import functools
import typing

import langgraph.graph
import langgraph.types

import domains

from ... import nodes
from . import entities, states


def _identity(state: states.DistillRunState) -> dict[str, typing.Any]:
    return {
        key: state[key]
        for key in (
            "book_id", "run_id", "extraction_run_id", "ingestion_ai_run_id", "source_manifest_key",
            "source_manifest_sha256", "ingestion_ai_manifest_key", "ingestion_ai_manifest_sha256",
            "content_selection_key", "content_selection_sha256", "content_selection_revision",
            "expected_page_count", "selected_page_count", "run_created_at",
        )
        if key in state
    }


def _dispatch_units(state: states.DistillRunState) -> list[langgraph.types.Send] | str:
    refs = state.get("unit_refs", [])
    if not refs:
        return "prepare_chapters"
    size = domains.bd.distill.params.FANOUT_UNIT_BATCH_SIZE
    return [
        langgraph.types.Send("extract_unit", {**_identity(state), "unit_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


def _dispatch_chapters(state: states.DistillRunState) -> list[langgraph.types.Send] | str:
    refs = state.get("chapter_refs", [])
    if not refs:
        raise ValueError("the completed extraction did not contain readable learning material")
    size = domains.bd.distill.params.FANOUT_CHAPTER_BATCH_SIZE
    return [
        langgraph.types.Send("synthesize_chapter", {**_identity(state), "chapter_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


def distill_graph(
    dependencies: entities.DistillDependencies,
    checkpointer: typing.Any | None = None,
):
    provider_retry_policy = langgraph.types.RetryPolicy(
        max_attempts = domains.bd.distill.params.TRANSIENT_NODE_RETRY_ATTEMPTS,
        initial_interval = domains.bd.distill.params.TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S,
        backoff_factor = 2.0,
        max_interval = domains.bd.distill.params.TRANSIENT_NODE_RETRY_MAX_INTERVAL_S,
        jitter = True,
        retry_on = domains.settings.chat.domain.is_transient_provider_error,
    )
    workflow = langgraph.graph.StateGraph(states.DistillRunState)
    workflow.add_node("prepare_units", functools.partial(nodes.distill.extract_learning_units.node.prepare_units, dependencies))
    workflow.add_node(
        "extract_unit",
        functools.partial(nodes.distill.extract_learning_units.node.extract_unit, dependencies),
        retry_policy = provider_retry_policy,
    )
    workflow.add_node("prepare_chapters", functools.partial(nodes.distill.synthesize.node.prepare_chapters, dependencies))
    workflow.add_node(
        "synthesize_chapter",
        functools.partial(nodes.distill.synthesize.node.synthesize_chapter, dependencies),
        retry_policy = provider_retry_policy,
    )
    workflow.add_node(
        "synthesize_book",
        functools.partial(nodes.distill.synthesize.node.synthesize_book, dependencies),
        retry_policy = provider_retry_policy,
    )
    workflow.add_node("publish_report", functools.partial(nodes.distill.publish_report.node.publish_report, dependencies))
    workflow.add_edge(langgraph.graph.START, "prepare_units")
    workflow.add_conditional_edges("prepare_units", _dispatch_units, ["extract_unit", "prepare_chapters"])
    workflow.add_edge("extract_unit", "prepare_chapters")
    workflow.add_conditional_edges("prepare_chapters", _dispatch_chapters, ["synthesize_chapter", "synthesize_book"])
    workflow.add_edge("synthesize_chapter", "synthesize_book")
    workflow.add_edge("synthesize_book", "publish_report")
    workflow.add_edge("publish_report", langgraph.graph.END)
    return workflow.compile(checkpointer = checkpointer)
