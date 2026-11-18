"""Deterministic routing rules for the retrieval-index graph."""
from __future__ import annotations

from typing import Any


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
