"""Runtime dependencies for one adaptive BD Ask graph invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AskRuntime:
    graph: Any
    context: Any
