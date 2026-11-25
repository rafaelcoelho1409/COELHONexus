"""Dependencies injected into a single Distill graph run."""
from __future__ import annotations
import infra

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


ProgressReporter = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots = True)
class DistillRunControls:
    """Mutable, run-scoped controls shared by parallel graph nodes."""

    chapter_synthesis_semaphore: asyncio.Semaphore
    chapter_settle_lock: asyncio.Lock = field(default_factory = asyncio.Lock)
    chapter_pool_settled: bool = False
    node_execution_lock: asyncio.Lock = field(default_factory = asyncio.Lock)
    active_node_executions: dict[str, int] = field(default_factory = dict)
    failed_node_executions: set[str] = field(default_factory = set)


@dataclass(frozen = True, slots = True)
class DistillDependencies:
    storage: infra.minio.service.MinIOStorage
    learning_extractor: Any
    evidence_verifier: Any
    chapter_synthesizer: Any
    visual_artifact_builder: Any
    book_synthesizer: Any
    decision_brief_writer: Any
    research_analyst: Any
    research_search_session: Any
    report_reviewer: Any
    controls: DistillRunControls
    progress_callback: ProgressReporter | None = None
    research_enabled: bool = False
