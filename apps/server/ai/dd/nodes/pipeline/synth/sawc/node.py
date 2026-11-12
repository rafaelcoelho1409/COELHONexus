"""sawc_write graph node: runs the sawc subgraph (a section_writer per section, stage by stage, then the top-ups), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@domains.dd.pipeline.observability.service.traced_synth("sawc_write")
async def sawc_write(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.sawc.graphs.sawc_graph().ainvoke(
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


async def prepare(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def start_stage(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.start_stage(state, runtime)


async def write_section(
    state: dict,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.write_section(state, runtime)


async def finish_stage(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finish_stage(state, runtime)


async def plan_coverage(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.plan_coverage(state, runtime)


async def extend_coverage(
    state: dict,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.extend_coverage(state, runtime)


async def merge_coverage(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.merge_coverage(state, runtime)


async def plan_sources(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.plan_sources(state, runtime)


async def extend_sources(
    state: dict,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.extend_sources(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.sawc.states.SawcState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.sawc.states.SawcState) -> str:
    return END if graph_state.get("finished") else "start_stage"


def write_fan_out(graph_state: graphs.pipeline.synth.sawc.states.SawcState):
    """One `write_section` branch per section the stage has to write; none (a stage fully reused) → finish_stage."""
    todo = graph_state.get("todo") or []
    if not todo:
        return "finish_stage"
    return [Send("write_section", {"task": task, "stage_pos": graph_state.get("stage_pos", 0)}) for task in todo]


def after_finish(graph_state: graphs.pipeline.synth.sawc.states.SawcState) -> str:
    """After the late retry: top-ups. After a stage: the next stage, then the late retry."""
    return "plan_coverage" if graph_state.get("phase") == "late" else "start_stage"


def coverage_fan_out(graph_state: graphs.pipeline.synth.sawc.states.SawcState):
    """One `extend_coverage` branch per section of this round; all rounds done → the source top-up."""
    if graph_state.get("topup_done"):
        return "plan_sources"
    ids = graph_state.get("extend_todo") or []
    if not ids:
        return "merge_coverage"
    return [Send("extend_coverage", {"sid": sid}) for sid in ids]


def sources_fan_out(graph_state: graphs.pipeline.synth.sawc.states.SawcState):
    ids = graph_state.get("extend_todo") or []
    if not ids:
        return "finalize"
    return [Send("extend_sources", {"sid": sid}) for sid in ids]
