"""Invoke the editorial-review model during Learning Guide publication."""
from __future__ import annotations
import domains
from .... import agents, graphs


async def publish_report(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict,
) -> dict:
    """Provide the domain publisher with its optional typed editorial-review callback."""
    async def review_report(packet: dict):
        return await agents.distill.report_reviewer.service.review_packet(
            dependencies.report_reviewer,
            packet,
            run_id = state["run_id"],
            context_kind = "publication",
        )

    async def report_unit_progress(unit_id: str, status: str, total_count: int) -> None:
        await graphs.service.report_node_item_progress(
            dependencies.progress_callback,
            "publish_report",
            unit_id,
            status,
            total_count = total_count,
        )

    return await domains.bd.distill.publish_report.service.publish_report(
        dependencies.storage,
        state,
        review_report = review_report,
        on_unit_progress = report_unit_progress,
    )
