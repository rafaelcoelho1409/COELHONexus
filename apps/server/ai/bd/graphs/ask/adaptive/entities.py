"""Runtime-only dependencies for one BD Ask graph invocation."""
from __future__ import annotations

import dataclasses
import typing


@dataclasses.dataclass(frozen = True, slots = True)
class AskContext:
    book_id: str
    run_id: str
    planner: typing.Any
    grader: typing.Any
    answerer: typing.Any
    critic: typing.Any
