"""Planner LangGraph — 8 sequential nodes, AsyncPostgresSaver-checkpointed.

Each substep is its own LangGraph node so we get one checkpoint after
each, one top-level OTel span (and therefore one LangFuse observation)
per substep, and one /debug/graph/{thread_id}/replay?checkpoint_id=...
target per substep. See project_planner_split_nodes_decision.md.

Conditional edge: cache_lookup → plan_write on cache hit, → map on miss.
"""
from __future__ import annotations

import logging
from typing import Optional

from langgraph.graph import END, START, StateGraph

from .checkpoint import get_checkpointer
from .nodes.cache_lookup import cache_lookup
from .nodes.corpus_load import corpus_load
from .nodes.dedup import dedup
from .nodes.map import map_node
from .nodes.off_topic import off_topic
from .nodes.plan_write import plan_write
from .nodes.reduce import reduce_node
from .nodes.validate import validate
from .state import PlannerState


logger = logging.getLogger(__name__)


def _route_after_cache_lookup(state: PlannerState) -> str:
    """Cache hit short-circuits the heavy MAP+REDUCE+VALIDATE path."""
    return "plan_write" if state.get("cached_plan") else "map"


def build_graph():
    """Build + compile the planner graph with the shared AsyncPostgresSaver.

    Returns the compiled graph. Each call returns a fresh compiled graph
    bound to the same checkpointer instance.
    """
    g = StateGraph(PlannerState)

    g.add_node("corpus_load", corpus_load)
    g.add_node("off_topic", off_topic)
    g.add_node("dedup", dedup)
    g.add_node("cache_lookup", cache_lookup)
    g.add_node("map", map_node)
    g.add_node("reduce", reduce_node)
    g.add_node("validate", validate)
    g.add_node("plan_write", plan_write)

    g.add_edge(START, "corpus_load")
    g.add_edge("corpus_load", "off_topic")
    g.add_edge("off_topic", "dedup")
    g.add_edge("dedup", "cache_lookup")
    g.add_conditional_edges(
        "cache_lookup",
        _route_after_cache_lookup,
        {"map": "map", "plan_write": "plan_write"},
    )
    g.add_edge("map", "reduce")
    g.add_edge("reduce", "validate")
    g.add_edge("validate", "plan_write")
    g.add_edge("plan_write", END)

    return g.compile(checkpointer=get_checkpointer())
