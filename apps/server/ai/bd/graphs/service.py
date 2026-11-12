"""Progress reporting shared by Book Distiller graphs and their nodes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any


ProgressReporter = Callable[[dict[str, Any]], Any]


async def report(progress_callback: ProgressReporter | None, **event: Any) -> None:
    if progress_callback is None:
        return
    result = progress_callback(event)
    if isawaitable(result):
        await result


async def run_node(
    progress_callback: ProgressReporter | None,
    node: str,
    operation: Callable[[], dict | Awaitable[dict]],
) -> dict:
    """Run one graph node's work and report it as running, then done, or failed if it raises."""
    await report(progress_callback, kind = "node", node = node, status = "running")
    try:
        result = operation()
        if isawaitable(result):
            result = await result
    except Exception:
        await report(progress_callback, kind = "node", node = node, status = "failed")
        raise
    await report(progress_callback, kind = "node", node = node, status = "done")
    return result
