"""sawc_derive graph node: runs the derive subgraph (a code_deriver agent per thin code block)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("sawc_derive")
async def sawc_derive(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.sawc_derive.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    return {"derive_stats": result.get("derive_stats") or {}}
