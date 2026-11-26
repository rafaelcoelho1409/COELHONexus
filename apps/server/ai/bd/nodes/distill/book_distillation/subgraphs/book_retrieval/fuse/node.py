"""Fuse retrieval evidence as a thin LangGraph node."""
from __future__ import annotations
from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def fuse_evidence(
    state: graphs.distill.book_distillation.states.DistillRunState,
    runtime: Runtime,
) -> dict:
    dependencies = runtime.context

    async def operation() -> dict:
        return await service.fuse_evidence(state, dependencies)

    if hasattr(dependencies, "progress_callback") and hasattr(dependencies, "controls"):
        return await graphs.service.run_node(
            dependencies.progress_callback,
            "fuse_evidence",
            operation,
            controls=dependencies.controls,
        )
    return await operation()
