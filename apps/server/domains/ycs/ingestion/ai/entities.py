"""Runtime dependencies composed for one YCS Neo4j extraction task."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class GraphExtractor:
    graph: Any
    agent: Any
    run_id: str | None = None
