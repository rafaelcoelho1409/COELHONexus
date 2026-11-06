"""checklist_eval graph node: runs the checklist subgraph (the deterministic gates and the chapter_judge agent)."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from ..... import graphs

from langgraph.runtime import Runtime


@traced_synth("checklist_eval")
async def checklist_eval(
    state: graphs.pipeline.synth.state.SynthState,
    runtime: Runtime[graphs.pipeline.synth.context.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.checklist.graph.build().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "chapter_id": state.get("chapter_id") or "",
            "thread_id": state.get("thread_id") or "",
            "sawc_stats": state.get("sawc_stats"),
            "best_seen_score": state.get("best_seen_score"),
            "best_seen_pregate": state.get("best_seen_pregate"),
            "best_seen_sawc_path": state.get("best_seen_sawc_path"),
            "consecutive_infra_degraded": state.get("consecutive_infra_degraded"),
        },
        context = runtime.context,
    )
    patch = {"checklist_path": result.get("checklist_path"), "checklist_stats": result.get("checklist_stats") or {}}
    for field in ("consecutive_infra_degraded", "best_seen_score", "best_seen_pregate", "best_seen_sawc_path"):
        if result.get(field) is not None:
            patch[field] = result[field]
    if result.get("status") == "failed":
        patch.update({"status": "failed", "error": result.get("error")})
    return patch
