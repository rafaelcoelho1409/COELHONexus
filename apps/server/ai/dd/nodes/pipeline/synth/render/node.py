"""render_audit_write graph node: runs the render subgraph (a code_normalizer per distinct code block, then the deterministic render, audit and writes)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("render_audit_write")
async def render_audit_write(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.render.graph.build().ainvoke(
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
    if result.get("status") in ("failed", "audit_failed"):
        patch.update({"status": result["status"], "error": result.get("error")})
    return patch
