"""Dependencies injected into the graph extraction subgraph."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class GraphExtractionContext:
    agent: Any
    run_id: str | None = None
