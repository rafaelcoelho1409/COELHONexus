"""outline_sdp graph node: runs the outline subgraph (the chapter_outliner agent, its validation tool and the deterministic passes), and the routes between its steps."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import domain, params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime


@traced_synth("outline_sdp")
async def outline_sdp(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.outline.graphs.outline_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
        },
        context = runtime.context,
    )
    patch = {"outline_path": result.get("outline_path"), "outline_stats": result.get("outline_stats") or {}}
    if result.get("status") == "failed":
        patch.update({"status": "failed", "error": result.get("error")})
    return patch


async def prepare(
    state: graphs.pipeline.synth.outline.states.OutlineState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def draft(
    state: graphs.pipeline.synth.outline.states.OutlineState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.draft(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.outline.states.OutlineState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.outline.states.OutlineState) -> str:
    return END if graph_state.get("finished") else "draft"


def after_draft(graph_state: graphs.pipeline.synth.outline.states.OutlineState) -> str:
    """Another run while the best outline still has issues (or there is none) and the attempts are not spent."""
    best = graph_state.get("best")
    issues = (best or {}).get("issues") or []
    attempts = graph_state.get("attempt", 0)
    floor_open = any(domain.is_floor_issue(issue) for issue in issues)
    limit = params.MAX_ATTEMPTS + (params.FLOOR_REPAIR_EXTRA if floor_open else 0)
    if (best is None or issues) and attempts < limit:
        return "draft"
    return "finalize"
