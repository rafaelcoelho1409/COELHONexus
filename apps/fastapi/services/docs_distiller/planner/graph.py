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
from typing import Optional

from langgraph.graph import END, START, StateGraph

from .checkpoint import get_checkpointer
from .nodes.cluster import cluster
from .nodes.corpus_load import corpus_load
from .nodes.dedup import dedup
from .nodes.embed_corpus import embed_corpus
from .nodes.map import map_node
from .nodes.off_topic import off_topic
from .nodes.plan_write import plan_write
from .nodes.reduce import reduce_node
from .nodes.refine import refine
from .nodes.validate import validate
from .state import PlannerState


logger = logging.getLogger(__name__)


# Canonical substep order. Every node listed here MUST be wired below in
# `NODE_REGISTRY` and listed in `IMPLEMENTED` to be included in the graph.
NODE_ORDER = (
    "corpus_load",
    "embed_corpus",
    "off_topic",
    "cluster",
    "refine",
    "dedup",
    "map",
    "reduce",
    "validate",
    "plan_write",
)

NODE_REGISTRY = {
    "corpus_load":  corpus_load,
    "embed_corpus": embed_corpus,
    "off_topic":    off_topic,
    "cluster":      cluster,
    "dedup":        dedup,
    "refine":       refine,
    "map":          map_node,
    "reduce":       reduce_node,
    "validate":     validate,
    "plan_write":   plan_write,
}

# ONLY these nodes are wired into the runtime graph. Order must match
# NODE_ORDER (prefix-contiguous from corpus_load). Append a name here
# as that substep's real (non-stub) implementation lands.
IMPLEMENTED = (
    "corpus_load",
    "embed_corpus",
    "off_topic",
    "cluster",
    "refine",
)


def build_graph():
    """Build + compile the planner graph with the shared AsyncPostgresSaver.
    Only nodes in `IMPLEMENTED` get wired; the others are tracked in the
    catalog (NODE_ORDER) for the UI but skipped at runtime.

    cache_lookup (the v1 early-exit node) was removed 2026-05-18 — its
    role is now covered by the smart Start Planner flow: client checks
    /planner/recent → reuses existing thread → graph.ainvoke(None, config)
    → LangGraph compares channel versions and skips committed nodes
    automatically. No special routing edge needed."""
    active = [n for n in NODE_ORDER if n in IMPLEMENTED]
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
