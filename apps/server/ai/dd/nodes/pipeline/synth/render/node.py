"""render_audit_write graph node: runs the render subgraph (a code_normalizer per distinct code block, then the deterministic render, audit and writes), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

import langgraph.graph
import langgraph.runtime
import langgraph.types


@domains.dd.pipeline.observability.service.traced_synth("render_audit_write")
async def render_audit_write(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.render.graphs.render_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
            "best_seen_sawc_path": state.get("best_seen_sawc_path"),
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    patch = {"chapter_path": result.get("chapter_path"), "chapter_stats": result.get("chapter_stats") or {}}
    if result.get("status") in ("done", "failed", "audit_failed", "needs_review", "cancelled"):
        patch.update({"status": result["status"], "error": result.get("error")})
    return patch


async def prepare(
    state: graphs.pipeline.synth.render.states.RenderState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def normalize_block(
    state: dict,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.normalize_block(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.render.states.RenderState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.render.states.RenderState):
    if graph_state.get("finished"):
        return langgraph.graph.END
    todo = graph_state.get("todo") or []
    if not todo:
        return "finalize"
    return [langgraph.types.Send("normalize_block", {"hash": h}) for h in todo]
