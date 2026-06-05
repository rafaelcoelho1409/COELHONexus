"""Planner LangGraph — strictly sequential, AsyncPostgresSaver-checkpointed.

One node per substep → one checkpoint, one OTel span, one /debug/replay
target each. Only nodes in `IMPLEMENTED` are wired; the rest are
catalogued in NODE_ORDER for the UI but skipped at runtime.

LLM-first pipeline:
  corpus_load → embed_corpus → off_topic → doc_distill
    → chapter_propose → chapter_assign → chapter_select
    → order_chapters → plan_write
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from .nodes.chapter_assign.node import chapter_assign
from .nodes.chapter_propose.node import chapter_propose
from .nodes.chapter_select.node import chapter_select
from .runtime.checkpoint import get_checkpointer
from .nodes.corpus_load.node import corpus_load
from .nodes.doc_distill.node import doc_distill
from .nodes.embed_corpus.node import embed_corpus
from .nodes.off_topic.node import off_topic
from .nodes.order_chapters.node import order_chapters
from .nodes.plan_write.node import plan_write
from .state import PlannerState


logger = logging.getLogger(__name__)


# Canonical substep order. Every entry must also appear in NODE_REGISTRY
# and IMPLEMENTED to be wired into the runtime graph.
NODE_ORDER = (
    "corpus_load",
    "embed_corpus",
    "off_topic",
    "doc_distill",
    "chapter_propose",
    "chapter_assign",
    "chapter_select",
    "order_chapters",
    "plan_write",
)

NODE_REGISTRY = {
    "corpus_load":      corpus_load,
    "embed_corpus":     embed_corpus,
    "off_topic":        off_topic,
    "doc_distill":      doc_distill,
    "chapter_propose":  chapter_propose,
    "chapter_assign":   chapter_assign,
    "chapter_select":   chapter_select,
    "order_chapters":   order_chapters,
    "plan_write":       plan_write,
}

# Primary output field per node. /resume's catch-up path uses this to
# detect IMPLEMENTED nodes that haven't run for a thread that already
# reached END (LangGraph's ainvoke(None) would otherwise short-circuit).
NODE_TO_FIELD = {
    "corpus_load":      "raw_files",
    "embed_corpus":     "embeddings_ref",
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
