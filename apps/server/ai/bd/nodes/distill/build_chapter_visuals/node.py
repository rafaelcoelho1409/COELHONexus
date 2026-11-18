"""Fan out visual generation after the chapter guides have been saved."""
from __future__ import annotations
import domains
from . import service
from .... import graphs

from langgraph.runtime import Runtime
from langgraph.types import Send


def dispatch_chapters(state: graphs.distill.states.DistillRunState) -> list[Send] | str:
    refs = state.get("synthesized_chapters", [])
    if not refs:
        return "synthesize_book"
    size = domains.bd.distill.params.FANOUT_CHAPTER_BATCH_SIZE
    identity = domains.bd.distill.domain.graph_identity(state)
    return [
        Send("build_chapter_visuals", {**identity, "chapter_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


async def build_chapter_visuals(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        completed = []
        for ref in graph_state.get("chapter_batch", []):
            output = await service.build_chapter_visuals(
                dependencies,
                {**graph_state, "chapter_ref": ref},
            )
            completed.extend(output.get("completed_chapters", []))
        return {"completed_chapters": completed}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "build_chapter_visuals",
        execute,
        controls = dependencies.controls,
    )
