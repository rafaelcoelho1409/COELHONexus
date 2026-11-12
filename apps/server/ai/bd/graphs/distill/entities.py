"""Dependencies injected into a single Distill graph run."""
from __future__ import annotations

import asyncio
import collections.abc
import dataclasses
import typing

import infra

ProgressReporter = collections.abc.Callable[[dict[str, typing.Any]], collections.abc.Awaitable[None]]


@dataclasses.dataclass(slots = True)
class DistillRunControls:
    """Mutable, run-scoped controls shared by parallel graph nodes."""

    chapter_synthesis_semaphore: asyncio.Semaphore
    chapter_settle_lock: asyncio.Lock = dataclasses.field(default_factory = asyncio.Lock)
    chapter_pool_settled: bool = False


@dataclasses.dataclass(frozen = True, slots = True)
class DistillDependencies:
    storage: infra.minio.service.MinIOStorage
    extractor: typing.Any
    controls: DistillRunControls
    progress_callback: ProgressReporter | None = None
