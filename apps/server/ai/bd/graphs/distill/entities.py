"""Dependencies injected into a single Distill graph run."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import infra

ProgressReporter = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots = True)
class DistillRunControls:
    """Mutable, run-scoped controls shared by parallel graph nodes."""

    chapter_synthesis_semaphore: asyncio.Semaphore
    chapter_settle_lock: asyncio.Lock = field(default_factory = asyncio.Lock)
    chapter_pool_settled: bool = False


@dataclass(frozen = True, slots = True)
class DistillDependencies:
    storage: infra.minio.service.MinIOStorage
    extractor: Any
    controls: DistillRunControls
    progress_callback: ProgressReporter | None = None
