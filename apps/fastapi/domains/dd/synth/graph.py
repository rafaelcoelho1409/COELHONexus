"""Synth LangGraph — per-chapter sequential nodes, AsyncPostgresSaver-checkpointed.

Each chapter gets its own thread_id and its own graph invocation. The
router fans out one graph per chapter when /synth/{slug} is hit (or runs
exactly one when /synth/{slug}/{chapter_id} is hit).

Reuses the planner's shared AsyncPostgresSaver — both pipelines write
into the same `checkpoints` tables but threads are namespaced by
`thread_id` prefix (`docs-distiller/planner/...` vs
`docs-distiller/synth/...`) so they coexist cleanly.

Incremental rollout matches the planner pattern:

  NODE_ORDER  — canonical 6-substep catalog from
                docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md
  IMPLEMENTED — prefix-contiguous subset wired into the runtime
  NODE_REGISTRY — name → coroutine table
  NODE_TO_FIELD — primary state output field per node (for /resume's
                  catch-up path; mirrors planner.NODE_TO_FIELD)

Add to IMPLEMENTED as each node ships. `outline_sdp` is the first
LLM-driven synth node; the 5 downstream nodes land incrementally.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from .checklist.node import checklist_eval
from .digest.node import digest_construct
from .mgsr.node import mgsr_replan
from .outline.node import outline_sdp
from .render.node import render_audit_write
from .sawc.node import sawc_write
from .sawc_derive.node import sawc_derive
# Planner owns checkpointer init in lifespan; reuse the same saver.
from ..planner.checkpoint import get_checkpointer
from .state import SynthState


logger = logging.getLogger(__name__)


# Canonical synth node order per SYNTH-ARCHITECTURE-SOTA-2026-05-18.md
# (after the 2026-05-19 reclassification — corpus_normalize +
# vault_sentinelize moved to ingestion-time, cache_lookup subsumed by
# per-stage MinIO content-addressed caches + LangGraph skip-completed).
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

# Primary state field each node writes — used by /resume's catch-up
# detector (mirror planner.NODE_TO_FIELD).
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


# =============================================================================
# CoRefine-style halting (2026-05-24) — mgsr_replan → sawc_write loop closure
# =============================================================================
# Per docs/KD-SYNTH-SOTA-2026-05-24.md §3 #4: replace the strictly-linear
# mgsr_replan → render_audit_write edge with a conditional edge:
#
#   HALT (success) — checklist pass_rate >= 0.80  → render_audit_write
#   HALT (budget)  — refine_iter >= 5             → render_audit_write
#   HALT (plateau) — iter >= 2 AND |score - prev| < 0.03 → render_audit_write
#   RETHINK        — otherwise                    → sawc_write (loop back)
#
# OP-12 best-seen rescue: handled inside sawc_write/render_audit_write —
# the state tracks best_seen_sawc_path so even after a budget/plateau halt
# we render the highest-scoring iteration.
#
# RefineBench Nov 2025 caveat: fixed-N Self-Refine plateaus or REGRESSES
# (+1.8pp GPT-5, -0.1pp DeepSeek-R1). The halting condition is what makes
# CoRefine work — without halting, this loop would be the same anti-pattern.
_CHECKLIST_THRESHOLD = 0.80
_MAX_REFINE_ITER = 5
_PLATEAU_DELTA = 0.03


def _route_after_mgsr(state: SynthState) -> str:
    """Conditional routing after mgsr_replan. Returns the next node name."""
    stats = state.get("checklist_stats") or {}
    score = float(stats.get("pass_rate", 0.0) or 0.0)
    refine_iter = int(state.get("refine_iter", 0) or 0)
    prev = state.get("prev_checklist_score")
    prev_score = float(prev) if isinstance(prev, (int, float)) else -1.0

    if score >= _CHECKLIST_THRESHOLD:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT success "
            f"(pass_rate={score:.2f} >= {_CHECKLIST_THRESHOLD})"
        )
        return "render_audit_write"

    if refine_iter >= _MAX_REFINE_ITER:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT budget "
            f"(refine_iter={refine_iter} >= {_MAX_REFINE_ITER}); "
            f"best-seen-rescue applies"
        )
        return "render_audit_write"

    if refine_iter >= 2 and abs(score - prev_score) < _PLATEAU_DELTA:
        logger.info(
            f"[synth-graph] {state.get('framework_slug')}/"
            f"{state.get('chapter_id')}: HALT plateau "
            f"(iter={refine_iter}, score={score:.2f}, prev={prev_score:.2f})"
        )
        return "render_audit_write"

    logger.info(
        f"[synth-graph] {state.get('framework_slug')}/"
        f"{state.get('chapter_id')}: RETHINK "
        f"(iter={refine_iter}, score={score:.2f}, threshold={_CHECKLIST_THRESHOLD}) "
        f"→ loop back to sawc_write"
    )
    return "sawc_write"


def build_graph():
    """Build + compile the synth graph with the shared AsyncPostgresSaver.
    Only nodes listed in IMPLEMENTED are wired.

    The mgsr_replan node has a conditional outgoing edge (CoRefine halting):
    loops back to sawc_write while the chapter has budget AND score < 0.80.
    """
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
    return g.compile(checkpointer=get_checkpointer())
