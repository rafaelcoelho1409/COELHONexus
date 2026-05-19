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

from .nodes.outline_sdp import outline_sdp
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
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)

NODE_REGISTRY = {
    "outline_sdp":        outline_sdp,
    # placeholders — add as each node ships:
    # "digest_construct":    digest_construct,
    # "sawc_write":          sawc_write,
    # "checklist_eval":      checklist_eval,
    # "mgsr_replan":         mgsr_replan,
    # "render_audit_write":  render_audit_write,
}

# Primary state field each node writes — used by /resume's catch-up
# detector (mirror planner.NODE_TO_FIELD).
NODE_TO_FIELD = {
    "outline_sdp":        "outline_path",
    "digest_construct":   "digest_path",
    "sawc_write":         "sawc_path",
    "checklist_eval":     "checklist_path",
    "mgsr_replan":        "mgsr_path",
    "render_audit_write": "chapter_path",
}

# ONLY these nodes are wired into the runtime. Append as each ships.
IMPLEMENTED = (
    "outline_sdp",
)


def build_graph():
    """Build + compile the synth graph with the shared AsyncPostgresSaver.
    Only nodes listed in IMPLEMENTED are wired."""
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
    for i in range(len(active) - 1):
        g.add_edge(active[i], active[i + 1])
    g.add_edge(active[-1], END)

    logger.info(
        f"[synth] graph compiled with {len(active)} active node(s): "
        f"{', '.join(active)}"
    )
    return g.compile(checkpointer=get_checkpointer())
