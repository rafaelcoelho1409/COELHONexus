"""digest_construct graph node: runs the digest subgraph (one source_router per source chunk, in parallel), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@domains.dd.pipeline.observability.service.traced_synth("digest_construct")
async def digest_construct(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.digest.graphs.digest_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    patch = {"digest_path": result.get("digest_path"), "digest_stats": result.get("digest_stats") or {}}
    if result.get("status") == "failed":
        patch.update({"status": "failed", "error": result.get("error")})
    return patch


async def prepare(
    state: graphs.pipeline.synth.digest.states.DigestState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def route_source(
    state: dict,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.route_source(state, runtime)


async def collect(
    state: graphs.pipeline.synth.digest.states.DigestState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.collect(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.digest.states.DigestState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def fan_out(graph_state: graphs.pipeline.synth.digest.states.DigestState):
    """One `route_source` branch per item still to route; none left → finalize."""
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    return [Send("route_source", {"item": item, "total": len(todo), "wave": graph_state.get("wave", 0)}) for item in todo]


def after_prepare(graph_state: graphs.pipeline.synth.digest.states.DigestState):
    if graph_state.get("finished"):
        return END
    return fan_out(graph_state)
