"""Invoke the editorial-review DeepAgent during Learning Guide publication."""
from __future__ import annotations

import json

import domains

from .... import graphs, skills


async def publish_report(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict,
) -> dict:
    """Provide the domain publisher with its optional typed editorial-review callback."""
    async def review_report(packet: dict):
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd", run_id = state["run_id"], node_id = "report_reviewer", stage = "distill",
        ):
            result = await domains.settings.chat.service.resilient_ainvoke(
                lambda: dependencies.report_reviewer.ainvoke({
                    "messages": [{
                        "role": "user",
                        "content": json.dumps(packet, ensure_ascii = False),
                    }],
                    "files": skills.service.files_for("distill_report_reviewer"),
                }, config = {
                    "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                }),
                attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
                wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
            )
        review = domains.bd.distill.domain.structured_model_from_agent_result(
            result,
            domains.bd.distill.schemas.ReportReviewOutput,
        )
        if not isinstance(review, domains.bd.distill.schemas.ReportReviewOutput):
            raise TypeError("report reviewer returned no valid ReportReviewOutput")
        return review

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
