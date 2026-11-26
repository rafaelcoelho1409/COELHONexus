"""Runtime-only dependencies for one BD Ask graph invocation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class AskContext:
    book_id: str
    run_id: str
    model: Any
    planner: Any
    grader: Any
    answerer: Any
    critic: Any
    counter_run_id: str | None = None
    counter_node_prefix: str = "ask_book_retrieval"
