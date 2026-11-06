"""outline_sdp graph node: runs the outline subgraph (the chapter_outliner agent, its validation tool and the deterministic passes)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("outline_sdp")
async def outline_sdp(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.outline.graph.build().ainvoke(
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
