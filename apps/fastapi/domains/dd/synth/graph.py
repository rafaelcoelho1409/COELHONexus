"""Synth LangGraph — per-chapter sequential nodes; shares AsyncPostgresSaver with planner (same tables, namespaced by thread_id prefix)."""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from ..planner.runtime.checkpoint import get_checkpointer
from .nodes.checklist.node import checklist_eval
from .nodes.digest.node import digest_construct
from .nodes.mgsr.node import mgsr_replan
from .nodes.outline.node import outline_sdp
from .params import (
    CHECKLIST_THRESHOLD,
    MAX_REFINE_ITER,
    NO_RECOVERY_FLOOR,
    PLATEAU_DELTA,
)
from .nodes.render.node import render_audit_write
from .nodes.sawc.node import sawc_write
from .nodes.sawc_derive.node import sawc_derive
from .state import SynthState


logger = logging.getLogger(__name__)


# Canonical synth node order (corpus_normalize + vault_sentinelize → ingestion-time; cache_lookup → per-stage MinIO caches).
NODE_ORDER = (
    "outline_sdp",
    "digest_construct",
    "sawc_write",
    "sawc_derive",
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)

NODE_REGISTRY = {
    "outline_sdp":        outline_sdp,
    "digest_construct":   digest_construct,
    "sawc_write":         sawc_write,
    "sawc_derive":        sawc_derive,
    "checklist_eval":     checklist_eval,
    "mgsr_replan":        mgsr_replan,
    "render_audit_write": render_audit_write,
}

# Primary state field each node writes — used by /resume's catch-up detector.
NODE_TO_FIELD = {
    "outline_sdp":        "outline_path",
    "digest_construct":   "digest_path",
    "sawc_write":         "sawc_path",
    "sawc_derive":        "derive_stats",
    "checklist_eval":     "checklist_path",
    "mgsr_replan":        "mgsr_path",
    "render_audit_write": "chapter_path",
}

# ONLY these nodes are wired into the runtime. Append as each ships.
IMPLEMENTED = (
    "outline_sdp",
    "digest_construct",
    "sawc_write",
    "sawc_derive",
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)


def _route_after_mgsr(state: SynthState) -> str:
    """CoRefine halting: success / budget / plateau / no-recovery → render; else loop."""
    stats = state.get("checklist_stats") or {}
    score = float(stats.get("pass_rate", 0.0) or 0.0)
    refine_iter = int(state.get("refine_iter", 0) or 0)
    prev = state.get("prev_checklist_score")
    prev_score = float(prev) if isinstance(prev, (int, float)) else -1.0

    if score >= CHECKLIST_THRESHOLD:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT success "
            f"(pass_rate={score:.2f} >= {CHECKLIST_THRESHOLD})"
        )
        return "render_audit_write"

    # iter-1 no-recovery short-circuit.
    if refine_iter <= 1 and score < NO_RECOVERY_FLOOR:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT no-recovery "
            f"(iter={refine_iter}, score={score:.2f} < {NO_RECOVERY_FLOOR}); "
            f"best-seen-rescue applies"
        )
        return "render_audit_write"

    if refine_iter >= MAX_REFINE_ITER:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT budget "
            f"(refine_iter={refine_iter} >= {MAX_REFINE_ITER}); "
            f"best-seen-rescue applies"
        )
        return "render_audit_write"

    if refine_iter >= 2 and abs(score - prev_score) < PLATEAU_DELTA:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT plateau "
            f"(iter={refine_iter}, score={score:.2f}, prev={prev_score:.2f})"
        )
        return "render_audit_write"

    logger.info(
        f"[synth-graph] {state.get('framework_slug')}/"
        f"{state.get('chapter_id')}: RETHINK "
        f"(iter={refine_iter}, score={score:.2f}, "
        f"threshold={CHECKLIST_THRESHOLD}) → loop back to sawc_write"
    )
    return "sawc_write"


def build_graph():
    """Build + compile the synth graph with the shared AsyncPostgresSaver."""
    active = [n for n in NODE_ORDER if n in IMPLEMENTED]
    if not active:
        raise RuntimeError(
            "synth graph has no IMPLEMENTED nodes — add at least one "
            "before invoking the graph"
        )

    g = StateGraph(SynthState)
    for name in active:
        g.add_node(name, NODE_REGISTRY[name])

    g.add_edge(START, active[0])

    # Wire linear edges, BUT skip mgsr_replan → render_audit_write — that
    # edge is conditional (loop or terminate).
    loop_active = (
        "sawc_write" in active
        and "mgsr_replan" in active
        and "render_audit_write" in active
    )
    for i in range(len(active) - 1):
        src = active[i]
        dst = active[i + 1]
        if loop_active and src == "mgsr_replan":
            continue  # conditional edge wired below
        g.add_edge(src, dst)

    if loop_active:
        g.add_conditional_edges(
            "mgsr_replan",
            _route_after_mgsr,
            {
                "sawc_write":         "sawc_write",
                "render_audit_write": "render_audit_write",
            },
        )

    g.add_edge(active[-1], END)

    logger.info(
        f"[synth] graph compiled with {len(active)} active node(s): "
        f"{', '.join(active)}"
        f"{' (CoRefine loop wired)' if loop_active else ''}"
    )
    return g.compile(checkpointer = get_checkpointer())
