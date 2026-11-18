"""Deterministic graph-state rules for retrieval-index node replay."""
from __future__ import annotations
from .. import params

from typing import Any


def replay_state(state: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Preserve upstream and sibling results while clearing this node and descendants."""
    if node_id not in params.REPLAY_DESCENDANTS:
        raise ValueError(f"unknown retrieval-index replay node {node_id!r}")
    updated = dict(state)
    for downstream_node in params.REPLAY_DESCENDANTS[node_id]:
        updated.update(params.NODE_OUTPUT_DEFAULTS[downstream_node])
    updated["replay_from_node"] = node_id
    return updated


def retained_llm_nodes(node_id: str) -> set[str]:
    """Return usage-node IDs that remain valid after replaying this graph node."""
    invalidated = {
        usage_node
        for graph_node in params.REPLAY_DESCENDANTS[node_id]
        for usage_node in params.LLM_COUNTER_NODES_BY_GRAPH_NODE[graph_node]
    }
    all_usage_nodes = {
        usage_node
        for nodes in params.LLM_COUNTER_NODES_BY_GRAPH_NODE.values()
        for usage_node in nodes
    }
    return all_usage_nodes - invalidated
