"""Planner LangGraph — sequential nodes, AsyncPostgresSaver-checkpointed.

Each substep is its own LangGraph node so we get one checkpoint after
each, one top-level OTel span (and therefore one LangFuse observation)
per substep, and one /debug/graph/{thread_id}/replay?checkpoint_id=...
target per substep. See project_planner_split_nodes_decision.md.

Incremental rollout: the graph wires ONLY nodes listed in `IMPLEMENTED`.
Stubs aren't run — clicking "Start Planner" only executes substeps that
have been fully transplanted, avoiding misleading "done" states and
prevent later-substep crashes when they depend on outputs the earlier
ones don't yet produce. Add a node's name to `IMPLEMENTED` (in order)
as soon as its real implementation lands.

Strictly sequential — cache_lookup was removed 2026-05-18 (its role is
now covered by smart Start Planner thread reuse + LangGraph's native
ainvoke(None) skip-completed-nodes behavior).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from langgraph.graph import END, START, StateGraph

from .chapter_assign.node import chapter_assign
from .chapter_propose.node import chapter_propose
from .chapter_select.node import chapter_select
from .checkpoint import get_checkpointer
from .cluster.node import cluster
from .corpus_load.node import corpus_load
from .doc_distill.node import doc_distill
from .embed_corpus.node import embed_corpus
from .label.node import label
from .off_topic.node import off_topic
from .order_chapters.node import order_chapters
from .plan_write.node import plan_write
from .reduce.node import reduce_node
from .refine.node import refine
from .state import PlannerState


logger = logging.getLogger(__name__)


# DD-PLANNER-LLM-FIRST-SOTA-2026-05-27 — KD_PLANNER_LLM_FIRST=true (default)
# routes corpus_load → embed_corpus → off_topic → doc_distill →
# chapter_propose → chapter_assign → chapter_select → order_chapters →
# plan_write. Legacy path (cluster→refine→label→reduce) kept as fallback;
# both end at `chapter_plan_ref` so order_chapters + plan_write are shared.
_LLM_FIRST = os.environ.get(
    "KD_PLANNER_LLM_FIRST", "true",
).lower() in ("true", "1", "yes", "on")


# Canonical substep order. Every node listed here MUST be wired below in
# `NODE_REGISTRY` and listed in `IMPLEMENTED` to be included in the graph.
# Two parallel sub-pipelines (cluster→reduce LEGACY vs doc_distill→
# chapter_select LLM-FIRST) converge at `order_chapters` / `plan_write`.
NODE_ORDER = (
    "corpus_load",
    "embed_corpus",
    "off_topic",
    # legacy path:
    "cluster",
    "refine",
    "label",
    "reduce",
    # LLM-first path (KD_PLANNER_LLM_FIRST=true, default):
    "doc_distill",
    "chapter_propose",
    "chapter_assign",
    "chapter_select",
    # shared tail:
    "order_chapters",
    "plan_write",
)

NODE_REGISTRY = {
    "corpus_load":      corpus_load,
    "embed_corpus":     embed_corpus,
    "off_topic":        off_topic,
    "cluster":          cluster,
    "refine":           refine,
    "label":            label,
    "reduce":           reduce_node,
    "doc_distill":      doc_distill,
    "chapter_propose":  chapter_propose,
    "chapter_assign":   chapter_assign,
    "chapter_select":   chapter_select,
    "order_chapters":   order_chapters,
    "plan_write":       plan_write,
}

# Primary state field each node writes. Used by /resume's catch-up
# path to detect IMPLEMENTED nodes that haven't run yet for a thread
# (e.g. when a node lands AFTER a thread already completed — LangGraph
# would otherwise short-circuit `ainvoke(None)` because the old
# checkpoint's END marker is already consumed). The catch-up code
# invokes the missing node directly through NODE_REGISTRY and patches
# state via `aupdate_state`, preserving SSE events end-to-end.
NODE_TO_FIELD = {
    "corpus_load":      "raw_files",
    "embed_corpus":     "embeddings_ref",
    "off_topic":        "relevant_files",
    "cluster":          "cluster_assignments_ref",
    "refine":           "refine_assignments_ref",
    "label":            "cluster_labels_ref",
    "reduce":           "chapter_plan_ref",
    "doc_distill":      "doc_distill_ref",
    "chapter_propose":  "chapter_proposals_ref",
    "chapter_assign":   "chapter_doc_assignments_ref",
    "chapter_select":   "chapter_plan_ref",         # same field as reduce
    "order_chapters":   "chapter_order_ref",
    "plan_write":       "plan_path",
}

# Which nodes are wired at runtime depends on KD_PLANNER_LLM_FIRST. The
# common prefix (corpus_load → embed_corpus → off_topic) is shared. The
# tail (order_chapters → plan_write) is shared. The middle differs.
_COMMON_HEAD = ("corpus_load", "embed_corpus", "off_topic")
_COMMON_TAIL = ("order_chapters", "plan_write")
_LEGACY_MIDDLE = ("cluster", "refine", "label", "reduce")
_LLM_FIRST_MIDDLE = (
    "doc_distill", "chapter_propose", "chapter_assign", "chapter_select",
)

IMPLEMENTED = (
    _COMMON_HEAD
    + (_LLM_FIRST_MIDDLE if _LLM_FIRST else _LEGACY_MIDDLE)
    + _COMMON_TAIL
)


def build_graph():
    """Build + compile the planner graph with the shared AsyncPostgresSaver.
    Only nodes in `IMPLEMENTED` get wired; the others are tracked in the
    catalog (NODE_ORDER) for the UI but skipped at runtime.

    Per KD_PLANNER_LLM_FIRST env flag, `IMPLEMENTED` is composed of either:
      - legacy: corpus_load → embed_corpus → off_topic → cluster → refine
                → label → reduce → order_chapters → plan_write
      - llm-first (default): corpus_load → embed_corpus → off_topic →
                doc_distill → chapter_propose → chapter_assign →
                chapter_select → order_chapters → plan_write

    cache_lookup (the v1 early-exit node) was removed 2026-05-18 — its
    role is now covered by the smart Start Planner flow: client checks
    /planner/recent → reuses existing thread → graph.ainvoke(None, config)
    → LangGraph compares channel versions and skips committed nodes
    automatically. No special routing edge needed."""
    active = [n for n in NODE_ORDER if n in IMPLEMENTED]
    logger.info(
        f"[planner] LLM-first mode: {_LLM_FIRST} "
        f"(env KD_PLANNER_LLM_FIRST)"
    )
    if not active:
        raise RuntimeError(
            "planner graph has no IMPLEMENTED nodes — add at least one "
            "before invoking the graph"
        )

    g = StateGraph(PlannerState)
    for name in active:
        g.add_node(name, NODE_REGISTRY[name])

    g.add_edge(START, active[0])
    for i in range(len(active) - 1):
        g.add_edge(active[i], active[i + 1])
    g.add_edge(active[-1], END)

    logger.info(
        f"[planner] graph compiled with {len(active)} active nodes: "
        f"{', '.join(active)}"
    )
    return g.compile(checkpointer=get_checkpointer())
