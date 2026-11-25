"""YCS knowledge graph extraction result types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphExtraction:
    """Graph facts projected from Docling Graph's validated models."""

    entities: list[dict[str, str]]
    relationships: list[dict[str, Any]]
    graph: Any
    provenance: Any
