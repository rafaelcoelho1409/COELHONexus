"""Thin graph wrapper for Learning Guide validation and publication."""
from __future__ import annotations
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


async def publish_report(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    return await publish_report_with_dependencies(runtime.context, graph_state)


async def publish_report_with_dependencies(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "publish_report",
        lambda: service.publish_report(dependencies, graph_state),
        controls = dependencies.controls,
    )
