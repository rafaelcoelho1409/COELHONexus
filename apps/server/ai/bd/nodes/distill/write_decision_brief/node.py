"""Write the separate Decision Brief graph stage."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def write_decision_brief(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "write_decision_brief",
        lambda: service.write_decision_brief(dependencies, graph_state),
        controls = dependencies.controls,
    )


async def write_decision_brief_in_report_pipeline(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    """Adapt the subgraph's private chapter list to the decision brief node input."""
    return await write_decision_brief(
        {**graph_state, "synthesized_chapters": graph_state.get("report_chapters", [])},
        runtime,
    )
