"""Extract source-supported learning units from a bounded source batch."""
from __future__ import annotations

from langgraph.runtime import Runtime

from .... import graphs
from . import service


async def extract_unit(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        extracted = []
        for ref in graph_state.get("unit_batch", []):
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "extract_unit",
                ref["unit_id"],
                "running",
            )
            output = await service.extract_unit(dependencies, {**graph_state, "unit_ref": ref})
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "extract_unit",
                ref["unit_id"],
                "done",
            )
            extracted.extend(output.get("extracted_units", []))
        return {"extracted_units": extracted}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "extract_unit",
        execute,
        controls = dependencies.controls,
    )
