"""Verify candidate lessons in bounded unit batches."""
from __future__ import annotations
import domains
from . import service
from .... import graphs

from langgraph.runtime import Runtime
from langgraph.types import Send


def dispatch_units(state: graphs.distill.states.DistillRunState) -> list[Send] | str:
    """Fan out extracted units for independent evidence verification."""
    refs = state.get("extracted_units", [])
    if not refs:
        return "prepare_chapters"
    size = domains.bd.distill.params.FANOUT_UNIT_BATCH_SIZE
    identity = domains.bd.distill.domain.graph_identity(state)
    return [
        Send("verify_unit", {**identity, "unit_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


async def verify_unit(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        completed = []
        for ref in graph_state.get("unit_batch", []):
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "verify_unit",
                ref["unit_id"],
                "running",
            )
            output = await service.verify_unit(dependencies, {**graph_state, "unit_ref": ref})
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "verify_unit",
                ref["unit_id"],
                "done",
            )
            completed.extend(output.get("completed_units", []))
        return {"completed_units": completed}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "verify_unit",
        execute,
        controls = dependencies.controls,
    )
