"""Extract source-supported learning units from a bounded source batch."""
from __future__ import annotations
import domains
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def extract_unit(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        return await domains.bd.distill.extract_learning_units.service.extract_unit_batch(
            graph_state,
            lambda unit_state: service.extract_unit(dependencies, unit_state),
        )

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "extract_unit",
        execute,
    )
