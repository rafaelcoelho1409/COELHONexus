"""sawc_write graph node: runs the sawc subgraph (a section_writer per section, stage by stage, then the top-ups)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import params
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("sawc_write")
async def sawc_write(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.sawc.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
            "synth_mode": state.get("synth_mode") or "quality",
            "refine_iter": state.get("refine_iter"),
            "checklist_stats": state.get("checklist_stats"),
            "best_seen_score": state.get("best_seen_score"),
            "best_seen_sawc_path": state.get("best_seen_sawc_path"),
        },
        {"max_concurrency": params.CONCURRENCY},
        context = runtime.context,
    )
    patch = {"sawc_path": result.get("sawc_path"), "sawc_stats": result.get("sawc_stats") or {}}
    for field in ("refine_iter", "best_seen_sawc_path", "best_seen_score", "prev_checklist_score"):
        if result.get(field) is not None:
            patch[field] = result[field]
    if result.get("status") == "failed":
        patch.update({"status": "failed", "error": result.get("error")})
    return patch
