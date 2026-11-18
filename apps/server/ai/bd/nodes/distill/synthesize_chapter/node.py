"""Synthesize a source-grounded chapter learning guide."""
from __future__ import annotations
import domains
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def synthesize_chapter(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def synthesize_one(
        chapter_state: dict,
        learning_units_by_id: dict[str, dict] | None,
    ) -> dict:
        return await service.synthesize_chapter(
            dependencies,
            chapter_state,
            learning_units_by_id = learning_units_by_id,
        )

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_chapter",
        lambda: domains.bd.distill.synthesize.service.synthesize_chapter_batch(
            dependencies.storage,
            graph_state,
            synthesize_one,
        ),
        controls = dependencies.controls,
    )
