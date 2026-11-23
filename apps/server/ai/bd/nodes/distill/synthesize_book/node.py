"""Synthesize the whole-book Learning Guide."""
from __future__ import annotations
import domains
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def synthesize_book(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_book",
        lambda: service.synthesize_book(dependencies, graph_state),
        controls = dependencies.controls,
    )


async def synthesize_book_in_report_pipeline(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    """Adapt the subgraph's private chapter list to the book synthesis node input."""
    return await synthesize_book(
        {**graph_state, "synthesized_chapters": graph_state.get("report_chapters", [])},
        runtime,
    )


def report_pipeline_entry(state: graphs.distill.states.DistillRunState) -> str:
    """Resume only the requested report stage, or skip this branch for visual-only replay."""
    target = state.get("replay_target_node")
    if target == "build_chapter_visuals":
        return "skip"
    if isinstance(target, str) and target in domains.bd.distill.params.REPORT_PIPELINE_NODES:
        return target
    return "synthesize_book"
