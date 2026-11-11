"""Runtime-only dependencies for one BD Ask graph invocation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class AskContext:
    book_id: str
    run_id: str
    planner: Any
    grader: Any
    answerer: Any
    critic: Any
