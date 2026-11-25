"""Fan out visual generation after the chapter guides have been saved."""
from __future__ import annotations
import domains
from . import service
from ..... import graphs

from langgraph.runtime import Runtime


async def build_chapter_visuals(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    if graph_state.get("replay_target_node") in domains.bd.distill.ai.params.REPORT_STAGE_NODES:
        return {}

    dependencies = runtime.context

    async def execute() -> dict:
        completed = []
        for ref in graph_state.get("chapter_batch", []):
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "build_chapter_visuals",
                ref["chapter_id"],
                "running",
            )
            output = await service.build_chapter_visuals(
                dependencies,
                {**graph_state, "chapter_ref": ref},
            )
            await graphs.service.report_node_item_progress(
                dependencies.progress_callback,
                "build_chapter_visuals",
                ref["chapter_id"],
                "done",
            )
            completed.extend(output.get("completed_chapters", []))
        return {"completed_chapters": completed}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "build_chapter_visuals",
        execute,
        controls = dependencies.controls,
    )
