"""Runtime dependencies for one source-specialist retrieval graph run."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class RetrievalContext:
    book_id: str
    run_id: str
    model: Any = None
