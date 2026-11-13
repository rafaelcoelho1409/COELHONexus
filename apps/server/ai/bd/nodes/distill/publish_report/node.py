"""Thin graph wrapper for Learning Guide validation and publication."""
from __future__ import annotations
from . import service
from .... import graphs

async def publish_report(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "publish_report",
        lambda: service.publish_report(dependencies, graph_state),
    )
