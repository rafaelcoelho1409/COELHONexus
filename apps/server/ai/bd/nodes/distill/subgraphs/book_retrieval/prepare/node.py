"""Prepare Distill retrieval state as a thin LangGraph node."""
from __future__ import annotations

from . import service
from ...... import graphs

from langgraph.runtime import Runtime


async def prepare_retrieval(
    state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_retrieval",
        lambda: service.prepare_retrieval_query(state, dependencies),
        controls=dependencies.controls,
    )
