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
    *,
    controls: Any | None = None,
) -> dict:
    """Run one graph node's work and report it as running, then done, or failed if it raises."""
    first_execution = True
    if controls is not None:
        async with controls.node_execution_lock:
            first_execution = controls.active_node_executions.get(node, 0) == 0
            controls.active_node_executions[node] = controls.active_node_executions.get(node, 0) + 1
            if first_execution:
                controls.failed_node_executions.discard(node)
    if first_execution:
        try:
            await report(progress_callback, kind = "node", node = node, status = "running")
        except BaseException:
            await _finish_node_execution(controls, node, failed = False, cancelled = True)
            raise

    async def finish(failed: bool, cancelled: bool = False) -> None:
        if controls is None:
            if first_execution and not cancelled:
                await report(
                    progress_callback,
                    kind = "node",
                    node = node,
                    status = "failed" if failed else "done",
                )
            return
        final_status = await _finish_node_execution(controls, node, failed, cancelled)
        if final_status is not None:
            await report(progress_callback, kind = "node", node = node, status = final_status)

    try:
        result = operation()
        if isawaitable(result):
            result = await result
    except BaseException as error:
        await finish(
            failed = isinstance(error, Exception),
            cancelled = not isinstance(error, Exception),
        )
        raise
    await finish(failed = False)
    return result


async def _finish_node_execution(
    controls: Any | None,
    node: str,
    failed: bool,
    cancelled: bool = False,
) -> str | None:
    if controls is None:
        return "failed" if failed else None if cancelled else "done"
    async with controls.node_execution_lock:
        if failed:
            controls.failed_node_executions.add(node)
        remaining = controls.active_node_executions.get(node, 1) - 1
        if remaining > 0:
            controls.active_node_executions[node] = remaining
            return None
        controls.active_node_executions.pop(node, None)
        if node in controls.failed_node_executions:
            controls.failed_node_executions.discard(node)
            return "failed"
        return None if cancelled else "done"
