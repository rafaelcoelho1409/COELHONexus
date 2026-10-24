"""LangGraph planner — strictly sequential; one AsyncPostgresSaver checkpoint + OTel span per node."""
from __future__ import annotations
import domains
from . import state, nodes

import logging

from langgraph.graph import END, START, StateGraph



logger = logging.getLogger(__name__)


# Canonical substep order. Every entry must also appear in NODE_REGISTRY
# and IMPLEMENTED to be wired into the runtime graph.
# embed_corpus removed 2026-09-03: off_topic now LLM-only (margins telemetry
# only), so corpus_load → off_topic → doc_distill is fastest; embed_corpus
# kept on disk but not wired.
NODE_ORDER = (
    "corpus_load",
    "off_topic",
    "doc_distill",
    "chapter_propose",
    "chapter_assign",
    "chapter_select",
    "order_chapters",
    "plan_write",
)

NODE_REGISTRY = {
    "corpus_load":      nodes.corpus_load.node.corpus_load,
    "off_topic":        nodes.off_topic.node.off_topic,
    "doc_distill":      nodes.doc_distill.node.doc_distill,
    "chapter_propose":  nodes.chapter_propose.node.chapter_propose,
    "chapter_assign":   nodes.chapter_assign.node.chapter_assign,
    "chapter_select":   nodes.chapter_select.node.chapter_select,
    "order_chapters":   nodes.order_chapters.node.order_chapters,
    "plan_write":       nodes.plan_write.node.plan_write,
}

# Primary output field per node. /resume's catch-up path uses this to
# detect IMPLEMENTED nodes that haven't run for a thread that already
# reached END (LangGraph's ainvoke(None) would otherwise short-circuit).
NODE_TO_FIELD = {
    "corpus_load":      "raw_files",
    "off_topic":        "relevant_files",
    "doc_distill":      "doc_distill_ref",
    "chapter_propose":  "chapter_proposals_ref",
    "chapter_assign":   "chapter_doc_assignments_ref",
    "chapter_select":   "chapter_plan_ref",
    "order_chapters":   "chapter_order_ref",
    "plan_write":       "plan_path",
}

IMPLEMENTED = tuple(NODE_ORDER)


def build_graph():
    """Build + compile the planner graph with the shared AsyncPostgresSaver.
    Only nodes in `IMPLEMENTED` get wired; others are catalogued only."""
    active = [n for n in NODE_ORDER if n in IMPLEMENTED]
    if not active:
        raise RuntimeError(
            "planner graph has no IMPLEMENTED nodes — add at least one "
            "before invoking the graph"
        )

    g = StateGraph(state.PlannerState)
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
    return g.compile(
        checkpointer=domains.dd.planner.runtime.checkpoint.service.get_checkpointer())
