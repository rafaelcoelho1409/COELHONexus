"""Deterministic routing and state-reset rules for retrieval-index replay."""
from __future__ import annotations

from typing import Any

from . import params


NODE_OUTPUT_DEFAULTS: dict[str, dict[str, Any]] = {
    "pin_sources": {"source_artifact_key": "", "pin_sources_pages_completed": 0},
    "elasticsearch": {
        "elasticsearch_index": "",
        "elasticsearch_document_count": 0,
        "elasticsearch_passage_index": "",
        "elasticsearch_passage_count": 0,
        "elasticsearch_pages_completed": 0,
    },
    "qdrant": {
        "qdrant_collection": "",
        "embedding_model": "",
        "embedding_dimensions": 0,
        "qdrant_point_count": 0,
        "qdrant_pages_completed": 0,
    },
    "neo4j": {
        "neo4j_node_count": 0,
        "neo4j_relationship_count": 0,
        "graph_batches_completed": 0,
        "graph_batches_total": 0,
        "neo4j_pages_completed": 0,
        "neo4j_stage": "idle",
        "neo4j_stage_completed": 0,
        "neo4j_stage_total": 0,
        "neo4j_node_write_batches_completed": 0,
        "neo4j_node_write_batches_total": 0,
        "neo4j_relationship_write_batches_completed": 0,
        "neo4j_relationship_write_batches_total": 0,
    },
    "publish_retrieval_index": {},
}


def dispatch_to_stores(state: dict[str, Any]) -> list[str]:
    """Run the normal parallel stores, or only the selected store when replaying a branch."""
    replay_from_node = state.get("replay_from_node")
    if replay_from_node in {"qdrant", "neo4j"}:
        return [replay_from_node]
    return ["qdrant", "neo4j"]


def publish_after_replayed_store(state: dict[str, Any], node_id: str) -> list[str]:
    """Let one replayed store continue to publication without waiting on its completed sibling."""
    if state.get("replay_from_node") == node_id:
        return ["publish_retrieval_index"]
    return []


def replay_state(state: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Preserve upstream and sibling results while clearing this node and its descendants."""
    if node_id not in params.REPLAY_DESCENDANTS:
        raise ValueError(f"unknown retrieval-index replay node {node_id!r}")
    updated = dict(state)
    for downstream_node in params.REPLAY_DESCENDANTS[node_id]:
        updated.update(NODE_OUTPUT_DEFAULTS[downstream_node])
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
