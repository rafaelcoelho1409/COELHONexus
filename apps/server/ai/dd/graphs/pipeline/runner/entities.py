"""What a worker binds to a run of a Pipeline graph."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Binding:
    """The run's ties to COELHO Cloud, built by the worker's composition root (`domains/dd/pipeline/ai`) so nothing here imports `infra`: the compiled graph over
    the shared checkpointer, object storage, a fresh run context, and the LangGraph config of a thread."""

    graph: Any
    blobs: Any
    context: Callable[[], Any]
    config: Callable[[str], dict]
