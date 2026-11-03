"""Deterministic Book Distiller ingestion graph builders."""
from __future__ import annotations
from . import state
from ... import nodes

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph


PageReader = Callable[[str], str | None | Awaitable[str | None]]


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
):
    """Compile the full-book, versioned and review-aware ingestion workflow."""

    async def audit_batch(graph_state: state.IngestionRunState) -> dict:
        return await nodes.ingestion.audit_batch.node.audit_pages(graph_state, read_page, auditor)

    async def plan_repairs(graph_state: state.IngestionRunState) -> dict:
        return nodes.ingestion.plan_repairs.node.plan_repairs(graph_state)

    async def repair_pages(graph_state: state.IngestionRunState) -> dict:
        return await nodes.ingestion.repair_pages.node.repair_pages(
            graph_state, read_page, write_artifact, specialists,
        )

    async def normalize_metadata(graph_state: state.IngestionRunState) -> dict:
        return await nodes.ingestion.metadata_normalization.node.normalize_metadata(
            graph_state, read_page, write_artifact, metadata_normalizer,
        )

    async def publish_run(graph_state: state.IngestionRunState) -> dict:
        return await nodes.ingestion.publish.node.publish(graph_state, write_artifact)

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
