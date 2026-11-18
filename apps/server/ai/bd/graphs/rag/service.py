"""Runtime helpers for the processed-book retrieval-index graph."""
from __future__ import annotations
from . import entities

from collections.abc import Awaitable, Callable
from typing import Any


async def run_index_node(
    context: entities.ProcessedBookIndexDependencies,
    node: str,
    operation: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Persist node lifecycle and propagate cancellation at safe node boundaries."""
    if await context.cancel_check():
        raise RuntimeError("processed-book retrieval indexing was cancelled")
    await context.progress_callback(node, "running", None)
    try:
        result = operation()
        if hasattr(result, "__await__"):
            result = await result
    except Exception:
        await context.progress_callback(node, "failed", None)
        raise
    await context.progress_callback(node, "done", result)
    return result
