"""Graph projection entities written to the BD Neo4j index."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class GraphNode:
    id: str
    kind: str
    name: str
    properties: dict[str, str | int | float | bool | None]


@dataclass(frozen = True, slots = True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    kind: str
    properties: dict[str, Any]
    semantic: bool = False
