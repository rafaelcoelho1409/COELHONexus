"""Prepare Distill source units and route unit extraction work."""
from __future__ import annotations
import domains
from .... import graphs

from langgraph.runtime import Runtime
from langgraph.types import Send


def after_start(state: graphs.distill.states.DistillRunState) -> str:
    """Resume from persisted unit inputs when a later-stage run is restarted."""
    if state.get("resume_from_node") == "publish_report" and all(
        field in state
        for field in ("book_synthesis_key", "source_pages", "completed_units", "completed_chapters")
    ):
        return "publish_report"
    if state.get("resume_from_node") in {
        "prepare_chapters",
        "synthesize_chapter",
        "review_chapter_coverage",
        "build_chapter_visuals",
        "synthesize_book",
        "write_decision_brief",
        "research_context",
        "publish_report",
    }:
        return "prepare_chapters"
    return "prepare_units"


def dispatch_units(state: graphs.distill.states.DistillRunState) -> list[Send] | str:
    """Fan out bounded unit batches or continue directly when no units were found."""
    refs = state.get("unit_refs", [])
    if not refs:
        return "prepare_chapters"
    size = domains.bd.distill.params.FANOUT_UNIT_BATCH_SIZE
    identity = domains.bd.distill.domain.graph_identity(state)
    return [
        Send("extract_unit", {**identity, "unit_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


async def prepare_units(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        async def report_page_progress(page_number: int, status: str, total_count: int) -> None:
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "prepare_units",
                str(page_number),
                status,
                total_count = total_count,
            )

        result = await domains.bd.distill.extract_learning_units.service.prepare_units(
            dependencies.storage,
            graph_state,
            on_page_progress = report_page_progress,
        )
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "unit_plan",
            total_units = len(result.get("unit_refs", [])),
        )
        return result

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_units",
        execute,
        controls = dependencies.controls,
    )
