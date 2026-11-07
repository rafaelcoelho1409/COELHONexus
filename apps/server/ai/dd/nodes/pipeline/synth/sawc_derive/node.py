"""sawc_derive graph node: runs the derive subgraph (a code_deriver agent per thin code block), and the routes between its steps."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@traced_synth("sawc_derive")
async def sawc_derive(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.sawc_derive.graphs.sawc_derive_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    return {"derive_stats": result.get("derive_stats") or {}}


async def prepare(
    state: graphs.pipeline.synth.sawc_derive.states.DeriveState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def derive_subtopic(
    state: dict,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.derive_subtopic(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.sawc_derive.states.DeriveState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.sawc_derive.states.DeriveState):
    if graph_state.get("finished"):
        return END
    count = graph_state.get("n_candidates", 0)
    if not count:
        return "finalize"
    return [Send("derive_subtopic", {"index": index}) for index in range(count)]
