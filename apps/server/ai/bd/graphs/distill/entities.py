"""Dependencies injected into a single Distill graph run."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import infra

ProgressReporter = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen = True, slots = True)
class DistillDependencies:
    storage: infra.minio.service.MinIOStorage
    extractor: Any
    progress_callback: ProgressReporter | None = None
