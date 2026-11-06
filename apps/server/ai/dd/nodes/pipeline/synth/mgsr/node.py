"""mgsr_replan graph node: runs the mgsr subgraph (the chapter_reviewer agent and its decision)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("mgsr_replan")
async def mgsr_replan(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.mgsr.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
            "refine_iter": state.get("refine_iter"),
        },
        context = runtime.context,
    )
    patch = {"mgsr_path": result.get("mgsr_path"), "mgsr_stats": result.get("mgsr_stats") or {}}
    if result.get("status") == "failed":
        patch.update({"status": "failed", "error": result.get("error")})
    return patch
