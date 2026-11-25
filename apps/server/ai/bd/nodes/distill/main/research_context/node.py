"""Gather optional external context as a separate graph stage."""
from __future__ import annotations
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


async def research_context(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "research_context",
        lambda: service.research_context(dependencies, graph_state),
        controls = dependencies.controls,
    )
