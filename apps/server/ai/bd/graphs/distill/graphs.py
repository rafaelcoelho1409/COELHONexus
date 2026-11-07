"""Build the source-only, resumable Distill workflow."""
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import domains

from ... import nodes
from . import entities, states


def _identity(state: states.DistillRunState) -> dict[str, Any]:
    return {
        key: state[key]
        for key in (
            "book_id", "run_id", "extraction_run_id", "ingestion_ai_run_id", "source_manifest_key",
            "source_manifest_sha256", "ingestion_ai_manifest_key", "ingestion_ai_manifest_sha256",
            "expected_page_count", "run_created_at",
        )
        if key in state
    }


def _dispatch_units(state: states.DistillRunState) -> list[Send] | str:
    refs = state.get("unit_refs", [])
    if not refs:
        return "prepare_chapters"
    size = domains.bd.distill.params.FANOUT_UNIT_BATCH_SIZE
    return [
        Send("extract_unit", {**_identity(state), "unit_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


def _dispatch_chapters(state: states.DistillRunState) -> list[Send] | str:
    refs = state.get("chapter_refs", [])
    if not refs:
        raise ValueError("the completed extraction did not contain readable learning material")
    size = domains.bd.distill.params.FANOUT_CHAPTER_BATCH_SIZE
    return [
        Send("synthesize_chapter", {**_identity(state), "chapter_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


def distill_graph(
    dependencies: entities.DistillDependencies,
    checkpointer: Any | None = None,
):
    workflow = StateGraph(states.DistillRunState)
    workflow.add_node("prepare_units", partial(nodes.distill.extract_learning_units.node.prepare_units, dependencies))
    workflow.add_node("extract_unit", partial(nodes.distill.extract_learning_units.node.extract_unit, dependencies))
    workflow.add_node("prepare_chapters", partial(nodes.distill.synthesize.node.prepare_chapters, dependencies))
    workflow.add_node("synthesize_chapter", partial(nodes.distill.synthesize.node.synthesize_chapter, dependencies))
    workflow.add_node("synthesize_book", partial(nodes.distill.synthesize.node.synthesize_book, dependencies))
    workflow.add_node("publish_report", partial(nodes.distill.publish_report.node.publish_report, dependencies))
    workflow.add_edge(START, "prepare_units")
    workflow.add_conditional_edges("prepare_units", _dispatch_units, ["extract_unit", "prepare_chapters"])
    workflow.add_edge("extract_unit", "prepare_chapters")
    workflow.add_conditional_edges("prepare_chapters", _dispatch_chapters, ["synthesize_chapter", "synthesize_book"])
    workflow.add_edge("synthesize_chapter", "synthesize_book")
    workflow.add_edge("synthesize_book", "publish_report")
    workflow.add_edge("publish_report", END)
    return workflow.compile(checkpointer = checkpointer)
