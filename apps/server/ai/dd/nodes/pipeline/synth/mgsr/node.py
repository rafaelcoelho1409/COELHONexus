"""mgsr_replan graph node: runs the mgsr subgraph (the chapter_reviewer agent and its decision), and the routes after it: between its own steps and, for the chapter, the CoRefine loop."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced_synth
from . import service
from ..... import graphs

import logging
import time

from langgraph.graph import END
from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)


@traced_synth("mgsr_replan")
async def mgsr_replan(
    state: graphs.pipeline.synth.states.SynthState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    result = await graphs.pipeline.synth.mgsr.graphs.mgsr_graph().ainvoke(
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


async def prepare(
    state: graphs.pipeline.synth.mgsr.states.MgsrState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def review(
    state: graphs.pipeline.synth.mgsr.states.MgsrState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.review(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.mgsr.states.MgsrState,
    runtime: Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.mgsr.states.MgsrState) -> str:
    if graph_state.get("finished"):
        return END
    return "finalize" if graph_state.get("trivial") else "review"


def route_after_mgsr(graph_state: graphs.pipeline.synth.states.SynthState) -> str:
    """CoRefine halting: success / budget / plateau / no-recovery / outage / wall clock → render; else loop to sawc_write."""
    where = f"[synth-graph] {graph_state.get('framework_slug')}/{graph_state.get('chapter_id')}"
    stats = graph_state.get("checklist_stats") or {}
    score = float(stats.get("pass_rate", 0.0) or 0.0)
    refine_iter = int(graph_state.get("refine_iter", 0) or 0)
    prev = graph_state.get("prev_checklist_score")
    prev_score = float(prev) if isinstance(prev, (int, float)) else -1.0

    if score >= graphs.pipeline.synth.params.CHECKLIST_THRESHOLD:
        logger.info(f"{where}: HALT success (pass_rate={score:.2f} >= {graphs.pipeline.synth.params.CHECKLIST_THRESHOLD})")
        return "render_audit_write"

    # iter-1 no-recovery short-circuit — but only when the low score is a genuine content judgment. A score this low driven by the judge
    # itself failing (Rotator outage, not real review) deserves the one RETHINK attempt this would otherwise skip, since the "rarely
    # recovers above 0.80" prior behind this floor was measured against genuine low-quality drafts, not judge-call failures.
    infra_degraded = bool(stats.get("infra_degraded", False))
    if refine_iter <= 1 and score < graphs.pipeline.synth.params.NO_RECOVERY_FLOOR and not infra_degraded:
        logger.info(
            f"{where}: HALT no-recovery (iter={refine_iter}, score={score:.2f} < {graphs.pipeline.synth.params.NO_RECOVERY_FLOOR}); "
            f"best-seen-rescue applies"
        )
        return "render_audit_write"
    if refine_iter <= 1 and score < graphs.pipeline.synth.params.NO_RECOVERY_FLOOR:
        logger.info(f"{where}: no-recovery floor tripped (score={score:.2f}) but infra_degraded=True — giving it the RETHINK attempt anyway")

    # Sustained-outage halt: N consecutive iterations degraded by the judge infra itself means the RETHINK loop isn't testing content
    # quality anymore — it's retrying against an outage that isn't clearing.
    outage = int(graph_state.get("consecutive_infra_degraded", 0) or 0)
    if outage >= graphs.pipeline.synth.params.SUSTAINED_INFRA_OUTAGE_LIMIT:
        logger.info(
            f"{where}: HALT sustained-outage ({outage} consecutive infra-degraded iterations >= "
            f"{graphs.pipeline.synth.params.SUSTAINED_INFRA_OUTAGE_LIMIT})"
        )
        return "render_audit_write"

    if refine_iter >= graphs.pipeline.synth.params.MAX_REFINE_ITER:
        logger.info(f"{where}: HALT budget (refine_iter={refine_iter} >= {graphs.pipeline.synth.params.MAX_REFINE_ITER}); best-seen-rescue applies")
        return "render_audit_write"

    if refine_iter >= 2 and abs(score - prev_score) < graphs.pipeline.synth.params.PLATEAU_DELTA:
        logger.info(f"{where}: HALT plateau (iter={refine_iter}, score={score:.2f}, prev={prev_score:.2f})")
        return "render_audit_write"

    # The reviewer found nothing a rewrite would fix: halt with the best draft so far.
    if (graph_state.get("mgsr_stats") or {}).get("halt_reason") == "reviewer_halt":
        logger.info(f"{where}: HALT reviewer (no rewrite would help); best-seen-rescue applies")
        return "render_audit_write"

    # Wall-clock RETHINK gate: another sawc_write iteration is about to start, but if it can't plausibly finish before this task's Celery
    # soft_time_limit fires, looping just spends the remaining budget on a doomed attempt. Absent run_started_at → skip the check.
    run_started_at = graph_state.get("run_started_at")
    if isinstance(run_started_at, (int, float)) and run_started_at > 0:
        elapsed = time.time() - run_started_at
        remaining = graphs.pipeline.synth.params.SINGLE_CHAPTER_SOFT_TIME_LIMIT_S - elapsed
        last_cost_ms = (graph_state.get("sawc_stats") or {}).get("wall_ms")
        estimated = (
            ((float(last_cost_ms) / 1000.0) if last_cost_ms else graphs.pipeline.synth.params.FALLBACK_SAWC_WRITE_COST_S)
            * graphs.pipeline.synth.params.WALL_CLOCK_SAFETY_MARGIN
        )
        if remaining < estimated:
            logger.info(
                f"{where}: HALT wall-clock-budget (elapsed={elapsed:.0f}s, remaining={remaining:.0f}s < estimated next iteration {estimated:.0f}s); "
                f"best-seen-rescue applies instead of risking a hard Celery kill with no output"
            )
            return "render_audit_write"

    logger.info(
        f"{where}: RETHINK (iter={refine_iter}, score={score:.2f}, threshold={graphs.pipeline.synth.params.CHECKLIST_THRESHOLD}) → loop back to sawc_write"
    )
    return "sawc_write"
