"""Runtime dependencies for the processed-book retrieval graph."""
from __future__ import annotations
import infra

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


ProgressReporter = Callable[[str, str, dict[str, Any] | None], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen = True, slots = True)
class ProcessedBookIndexDependencies:
    """Services required by nodes that index an immutable processed-book revision."""

    storage: infra.minio.service.MinIOStorage
    graph_extractor: Any
    progress_callback: ProgressReporter
    cancel_check: CancelCheck
