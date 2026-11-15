"""Checkpoint operations shared by persisted LangGraph workflows."""
from __future__ import annotations

from typing import Any


async def checkpoint_before_node(
    graph: Any,
    config: dict[str, Any],
    node_id: str,
) -> dict[str, Any]:
    """Return the newest checkpoint that schedules ``node_id`` next.

    LangGraph replay starts from that checkpoint, so upstream nodes stay
    complete and the selected node runs again. The checkpoint history remains
    available as an audit trail because this operation creates a new branch.
    """
    async for snapshot in graph.aget_state_history(config):
        next_nodes = tuple(snapshot.next or ())
        if node_id not in next_nodes:
            continue
        if len(next_nodes) > 1:
            raise ValueError(
                f"node {node_id!r} shares a pending checkpoint with other nodes; "
                "it cannot be replayed independently"
            )
        snapshot_config = snapshot.config
        if isinstance(snapshot_config, dict):
            return snapshot_config
    raise ValueError(f"no saved checkpoint schedules node {node_id!r}")
