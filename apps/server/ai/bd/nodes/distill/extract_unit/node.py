"""Extract source-supported learning units from a bounded source batch."""
from __future__ import annotations
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def extract_unit(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        extracted = []
        for ref in graph_state.get("unit_batch", []):
            output = await service.extract_unit(dependencies, {**graph_state, "unit_ref": ref})
            extracted.extend(output.get("extracted_units", []))
        return {"extracted_units": extracted}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "extract_unit",
        execute,
        controls = dependencies.controls,
    )
