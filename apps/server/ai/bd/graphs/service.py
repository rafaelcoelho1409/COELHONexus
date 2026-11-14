"""Progress reporting shared by Book Distiller graphs and their nodes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from langgraph.config import get_stream_writer


ProgressReporter = Callable[[dict[str, Any]], Any]


def mermaid_source(compiled_graph: Any) -> str:
    """Render the compiled graph's main topology as LangGraph Mermaid source."""
    return compiled_graph.get_graph().draw_mermaid()


def stream_progress_callback() -> ProgressReporter | None:
    """Return the active LangGraph custom-stream writer when one is available."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return None


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
