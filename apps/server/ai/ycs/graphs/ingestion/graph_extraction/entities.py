"""Dependencies injected into the graph extraction subgraph."""
from __future__ import annotations

import dataclasses
import typing


@dataclasses.dataclass(frozen = True, slots = True)
class GraphExtractionContext:
    agent: typing.Any
    run_id: str | None = None
