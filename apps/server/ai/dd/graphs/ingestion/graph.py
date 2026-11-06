"""Deterministic Docs Distiller page-ingestion graph builder."""
from __future__ import annotations
from . import state
from ... import nodes

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph


class ProgressReporter(Protocol):
    async def raise_if_cancelled(self) -> None: ...

    async def update(self, *, current: int) -> None: ...

    async def update_agentic_node(
        self,
        node: str,
        *,
        status: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None: ...


def build_ingestion_graph(
    read_page: nodes.ingestion.audit_batch.service.PageReader,
    artifact_exists: nodes.ingestion.audit_batch.service.ArtifactExists,
    read_artifact: nodes.ingestion.audit_batch.service.ArtifactReader,
    write_artifact: nodes.ingestion.audit_batch.service.ArtifactWriter,
    auditor: nodes.ingestion.audit_batch.service.Auditor,
    repairer: nodes.ingestion.normalize_pages.service.Repairer,
    progress: ProgressReporter | None = None,
    checkpointer: Any | None = None,
):
    """Compile the fixed audit, normalize, then publish workflow."""
    completed_pages = 0
    completed_by_node = {"audit_pages": 0, "normalize_pages": 0}
    progress_lock = asyncio.Lock()

    async def page_done(node: str, total: int) -> None:
        nonlocal completed_pages
        if progress is None:
            return
        async with progress_lock:
            completed_by_node[node] += 1
            await progress.update_agentic_node(
                node,
                current = completed_by_node[node],
                total = total,
            )
            if node == "normalize_pages":
                completed_pages += 1
                await progress.update(current = completed_pages)

    async def run_node(
        node: str,
        operation: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        if progress is not None:
            await progress.update_agentic_node(node, status = "running")
        try:
            result = await operation()
        except BaseException:
            if progress is not None:
                await progress.update_agentic_node(node, status = "failed")
            raise
        if progress is not None:
            await progress.update_agentic_node(node, status = "done")
        return result

    async def audit_pages(graph_state: state.IngestionState) -> dict[str, Any]:
        total = len(graph_state.get("pages", []))
        return await run_node(
            "audit_pages",
            lambda: nodes.ingestion.audit_batch.node.audit_pages(
                graph_state,
                read_page,
                artifact_exists,
                read_artifact,
                write_artifact,
                auditor,
                progress,
                lambda: page_done("audit_pages", total),
            ),
        )

    async def normalize_pages(graph_state: state.IngestionState) -> dict[str, Any]:
        total = len(graph_state.get("pages", []))
        return await run_node(
            "normalize_pages",
            lambda: nodes.ingestion.normalize_pages.node.normalize_pages(
                graph_state,
                read_page,
                artifact_exists,
                read_artifact,
                write_artifact,
                repairer,
                progress,
                lambda: page_done("normalize_pages", total),
            ),
        )

    async def publish(graph_state: state.IngestionState) -> dict[str, Any]:
        return await run_node(
            "publish",
            lambda: nodes.ingestion.publish.node.publish(graph_state, write_artifact),
        )

    workflow = StateGraph(state.IngestionState)
    workflow.add_node("audit_pages", audit_pages)
    workflow.add_node("normalize_pages", normalize_pages)
    workflow.add_node("publish", publish)
    workflow.add_edge(START, "audit_pages")
    workflow.add_edge("audit_pages", "normalize_pages")
    workflow.add_edge("normalize_pages", "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer = checkpointer)
