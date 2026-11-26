"""Synthesize the whole-book Learning Guide."""
from __future__ import annotations
from . import service
from ...... import graphs

from langgraph.runtime import Runtime


async def synthesize_book(
    graph_state: graphs.distill.book_distillation.states.DistillRunState,
    runtime: Runtime[graphs.distill.book_distillation.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_book",
        lambda: service.synthesize_book(dependencies, graph_state),
        controls = dependencies.controls,
    )
