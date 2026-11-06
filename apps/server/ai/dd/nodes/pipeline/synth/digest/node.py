"""digest_construct graph node: runs the digest subgraph (one source_router per source chunk, in parallel)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("digest_construct")
async def digest_construct(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.digest.graph.build().ainvoke(
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
