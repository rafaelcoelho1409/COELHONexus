"""Synthesize a source-grounded chapter learning guide."""
from __future__ import annotations

from langgraph.runtime import Runtime
from langgraph.types import Send

import domains

from .... import graphs
from . import service


def dispatch_parallel_stages(state: graphs.distill.states.DistillRunState) -> list[Send]:
    """Start visual enrichment and book synthesis after every chapter guide is available."""
    refs = state.get("synthesized_chapters", [])
    size = domains.bd.distill.params.FANOUT_CHAPTER_BATCH_SIZE
    identity = domains.bd.distill.domain.graph_identity(state)
    visual_tasks = [
        Send("build_chapter_visuals", {**identity, "chapter_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]
    if not visual_tasks:
        visual_tasks.append(Send("build_chapter_visuals", {**identity, "chapter_batch": []}))
    return [
        *visual_tasks,
        Send("synthesize_book", {**identity, "synthesized_chapters": refs}),
    ]


async def synthesize_chapter(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
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
        lambda: domains.bd.distill.synthesize.service.synthesize_chapter_batch(
            dependencies.storage,
            graph_state,
            synthesize_one,
        ),
        controls = dependencies.controls,
    )
