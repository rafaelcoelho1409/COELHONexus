"""Dependencies injected into a bounded book graph extraction run."""
from __future__ import annotations

import dataclasses
import typing


@dataclasses.dataclass(frozen = True, slots = True)
class GraphExtractionContext:
    agent: typing.Any
    run_id: str
    prompt_version: str
