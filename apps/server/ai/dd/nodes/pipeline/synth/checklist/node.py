"""checklist_eval graph node: runs the checklist subgraph (the deterministic gates and the chapter_judge agent), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import domain, params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@domains.dd.pipeline.observability.service.traced_synth("checklist_eval")
async def checklist_eval(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.checklist.graphs.checklist_graph().ainvoke(
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


async def prepare(
    state: graphs.pipeline.synth.checklist.states.ChecklistState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def judge(
    state: graphs.pipeline.synth.checklist.states.ChecklistState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.judge(state, runtime)


async def judge_again(
    state: dict,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.judge_again(state, runtime)


async def majority(
    state: graphs.pipeline.synth.checklist.states.ChecklistState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.majority(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.checklist.states.ChecklistState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.checklist.states.ChecklistState) -> str:
    return END if graph_state.get("finished") else "judge"


def after_judge(graph_state: graphs.pipeline.synth.checklist.states.ChecklistState):
    """A judge that failed (almost) every criterion, or whose verdict alone sends the chapter back for a rewrite, is checked by more runs."""
    run = graph_state.get("judge") or {}
    results = [domain.schemas.CriterionResult.model_validate(r) for r in run.get("results") or []]
    pre = graph_state.get("pre_results") or []
    if run.get("answered") and domain.wants_second_opinions(results, pre_passed = sum(1 for r in pre if r["passed"]), pre_total = len(pre)):
        return [Send("judge_again", {"run": n}) for n in range(1, params.JUDGE_SECOND_OPINIONS + 1)]
    return "finalize"
