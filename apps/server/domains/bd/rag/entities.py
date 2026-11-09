"""Value objects used to materialize evidence-linked book graphs."""
from __future__ import annotations

from dataclasses import dataclass


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
    evidence: str | None = None


@dataclass(frozen = True, slots = True)
class GraphSourceRef:
    page_number: int
    quote: str
    content_role: str = "unknown"
    content_disposition: str = "include"
