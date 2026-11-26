"""Synthesize a source-grounded chapter learning guide."""
from __future__ import annotations
import domains
from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def synthesize_chapter(
    graph_state: graphs.distill.book_distillation.states.DistillRunState,
    runtime: Runtime[graphs.distill.book_distillation.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def synthesize_one(
        chapter_state: dict,
        learning_units_by_id: dict[str, dict] | None,
    ) -> dict:
        chapter_id = chapter_state["chapter_ref"]["chapter_id"]
        await graphs.service.report_node_item_progress(
            dependencies.progress_callback,
            "synthesize_chapter",
            chapter_id,
            "running",
        )
        result = await service.synthesize_chapter(
            dependencies,
            chapter_state,
            learning_units_by_id = learning_units_by_id,
        )
        await graphs.service.report_node_item_progress(
            dependencies.progress_callback,
            "synthesize_chapter",
            chapter_id,
            "done",
        )
        return result

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_chapter",
        lambda: domains.bd.distill.ai.synthesize.service.synthesize_chapter_batch(
            dependencies.storage,
            graph_state,
            synthesize_one,
        ),
        controls = dependencies.controls,
    )
