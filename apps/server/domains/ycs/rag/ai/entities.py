"""Runtime dependencies for one YCS Neo4j graph-extraction task."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class GraphExtractorRuntime:
    """Model and run attribution shared by a task's transcript transforms."""

    model: Any
    run_id: str | None = None
