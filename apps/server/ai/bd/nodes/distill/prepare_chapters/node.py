"""Prepare chapter inputs and route chapter synthesis batches."""
from __future__ import annotations
import domains
from .... import graphs

from langgraph.runtime import Runtime
from langgraph.types import Send


def dispatch_chapters(state: graphs.distill.states.DistillRunState) -> list[Send]:
    """Fan out bounded chapter batches and reject empty extracted material."""
    refs = state.get("chapter_refs", [])
    if not refs:
        raise ValueError("the completed extraction did not contain readable learning material")
    size = domains.bd.distill.ai.params.FANOUT_CHAPTER_BATCH_SIZE
    identity = domains.bd.distill.ai.domain.graph_identity(state)
    return [
        Send("synthesize_chapter", {**identity, "chapter_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


async def prepare_chapters(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        await graphs.service.report_node_work_plan(
            dependencies.progress_callback,
            "prepare_chapters",
            len(graph_state.get("completed_units", [])),
        )

        async def report_unit_progress(unit_id: str, status: str, total_count: int) -> None:
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "prepare_chapters",
                unit_id,
                status,
                total_count = total_count,
            )

        result = await domains.bd.distill.ai.synthesize.service.prepare_chapters(
            dependencies.storage,
            graph_state,
            on_unit_progress = report_unit_progress,
        )
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "chapter_plan",
            total_chapters = len(result.get("chapter_refs", [])),
        )
        return result

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_chapters",
        execute,
        controls = dependencies.controls,
    )
