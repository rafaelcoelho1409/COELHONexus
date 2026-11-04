"""Deterministic Book Distiller ingestion graph builders."""
from __future__ import annotations
from . import state
from ... import nodes

from collections.abc import Awaitable, Callable
import inspect
from typing import Any

from langgraph.graph import END, START, StateGraph


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ProgressReporter = Callable[[dict[str, Any]], Any]


async def _report(progress_callback: ProgressReporter | None, **event: Any) -> None:
    if progress_callback is None:
        return
    result = progress_callback(event)
    if inspect.isawaitable(result):
        await result


def build_inventory_graph(
    read_page: PageReader,
    checkpointer: Any | None = None,
):
    """Compile a read-only graph that inventories pages from trusted manifest references."""

    async def inventory_pages(graph_state: state.IngestionInventoryState) -> dict:
        return await nodes.ingestion.inventory.node.inventory_pages(graph_state, read_page)

    workflow = StateGraph(state.IngestionInventoryState)
    workflow.add_node("inventory_pages", inventory_pages)
    workflow.add_edge(START, "inventory_pages")
    workflow.add_edge("inventory_pages", END)
    return workflow.compile(checkpointer = checkpointer)


def build_page_audit_graph(
    read_page: PageReader,
    auditor: nodes.ingestion.page_audit.node.PageAuditor,
    checkpointer: Any | None = None,
):
    """Compile a read-only, one-page audit graph with an injected auditor."""

    async def audit_page(graph_state: state.PageAuditState) -> dict:
        return await nodes.ingestion.page_audit.node.audit_page(
            graph_state,
            read_page,
            auditor,
        )

    workflow = StateGraph(state.PageAuditState)
    workflow.add_node("audit_page", audit_page)
    workflow.add_edge(START, "audit_page")
    workflow.add_edge("audit_page", END)
    return workflow.compile(checkpointer = checkpointer)


def build_ingestion_graph(
    read_page: PageReader,
    write_artifact: Callable[[str, str, str], Any],
    auditor: nodes.ingestion.audit_batch.service.Auditor,
    specialists: dict[str, nodes.ingestion.repair_pages.service.Specialist],
    metadata_normalizer: nodes.ingestion.metadata_normalization.service.Normalizer,
    checkpointer: Any | None = None,
    progress_callback: ProgressReporter | None = None,
):
    """Compile the full-book, versioned and review-aware ingestion workflow."""

    async def audit_batch(graph_state: state.IngestionRunState) -> dict:
        await _report(progress_callback, kind = "node", node = "audit_batch", status = "running")
        try:
            result = await nodes.ingestion.audit_batch.node.audit_pages(
                graph_state, read_page, auditor, progress_callback,
            )
        except Exception:
            await _report(progress_callback, kind = "node", node = "audit_batch", status = "failed")
            raise
        await _report(progress_callback, kind = "node", node = "audit_batch", status = "done")
        return result

    async def plan_repairs(graph_state: state.IngestionRunState) -> dict:
        await _report(progress_callback, kind = "node", node = "plan_repairs", status = "running")
        try:
            result = nodes.ingestion.plan_repairs.node.plan_repairs(graph_state)
        except Exception:
            await _report(progress_callback, kind = "node", node = "plan_repairs", status = "failed")
            raise
        await _report(progress_callback, kind = "node", node = "plan_repairs", status = "done")
        return result

    async def repair_pages(graph_state: state.IngestionRunState) -> dict:
        await _report(progress_callback, kind = "node", node = "repair_pages", status = "running")
        try:
            result = await nodes.ingestion.repair_pages.node.repair_pages(
                graph_state, read_page, write_artifact, specialists, progress_callback,
            )
        except Exception:
            await _report(progress_callback, kind = "node", node = "repair_pages", status = "failed")
            raise
        await _report(progress_callback, kind = "node", node = "repair_pages", status = "done")
        return result

    async def normalize_metadata(graph_state: state.IngestionRunState) -> dict:
        await _report(progress_callback, kind = "node", node = "normalize_metadata", status = "running")
        try:
            result = await nodes.ingestion.metadata_normalization.node.normalize_metadata(
                graph_state, read_page, write_artifact, metadata_normalizer,
            )
        except Exception:
            await _report(progress_callback, kind = "node", node = "normalize_metadata", status = "failed")
            raise
        await _report(progress_callback, kind = "node", node = "normalize_metadata", status = "done")
        return result

    async def publish_run(graph_state: state.IngestionRunState) -> dict:
        await _report(progress_callback, kind = "node", node = "publish", status = "running")
        try:
            result = await nodes.ingestion.publish.node.publish(graph_state, write_artifact)
        except Exception:
            await _report(progress_callback, kind = "node", node = "publish", status = "failed")
            raise
        await _report(progress_callback, kind = "node", node = "publish", status = "done")
        return result

    workflow = StateGraph(state.IngestionRunState)
    workflow.add_node("audit_batch", audit_batch)
    workflow.add_node("plan_repairs", plan_repairs)
    workflow.add_node("repair_pages", repair_pages)
    workflow.add_node("normalize_metadata", normalize_metadata)
    workflow.add_node("publish", publish_run)
    workflow.add_edge(START, "audit_batch")
    workflow.add_edge("audit_batch", "plan_repairs")
    workflow.add_edge("plan_repairs", "repair_pages")
    workflow.add_edge("repair_pages", "normalize_metadata")
    workflow.add_edge("normalize_metadata", "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer = checkpointer)
