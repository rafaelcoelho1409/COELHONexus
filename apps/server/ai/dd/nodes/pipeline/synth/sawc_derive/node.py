"""sawc_derive graph node: runs the derive subgraph (a code_deriver agent per thin code block), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

import langgraph.graph
import langgraph.runtime
import langgraph.types


@domains.dd.pipeline.observability.service.traced_synth("sawc_derive")
async def sawc_derive(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
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
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def derive_subtopic(
    state: dict,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.derive_subtopic(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.sawc_derive.states.DeriveState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


async def capstone(
    state: graphs.pipeline.synth.sawc_derive.states.DeriveState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.capstone(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.sawc_derive.states.DeriveState):
    if graph_state.get("finished"):
        return langgraph.graph.END
    count = graph_state.get("n_candidates", 0)
    if not count:
        return "finalize"
    return [langgraph.types.Send("derive_subtopic", {"index": index}) for index in range(count)]
