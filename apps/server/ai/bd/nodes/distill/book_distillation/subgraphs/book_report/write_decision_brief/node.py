"""Write the separate Decision Brief graph stage."""
from __future__ import annotations
from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def write_decision_brief(
    graph_state: graphs.distill.book_distillation.states.DistillRunState,
    runtime: Runtime[graphs.distill.book_distillation.entities.DistillDependencies],
) -> dict:
    if graph_state.get("replay_target_node") == "build_chapter_visuals":
        return {}
    dependencies = runtime.context
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "write_decision_brief",
        lambda: service.write_decision_brief(dependencies, graph_state),
        controls = dependencies.controls,
    )
