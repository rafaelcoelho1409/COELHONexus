"""Deterministic Book Distiller ingestion graph builder."""
from __future__ import annotations
from . import entities, service, state
from ... import graphs, nodes

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph


def ingestion_graph(
    read_page: entities.PageReader,
    write_artifact: entities.ArtifactWriter,
    auditor: nodes.ingestion.audit_batch.service.Auditor,
    specialists: dict[str, nodes.ingestion.repair_pages.service.Specialist],
    metadata_normalizer: nodes.ingestion.metadata_normalization.service.Normalizer,
    checkpointer: Any | None = None,
    progress_callback: graphs.service.ProgressReporter | None = None,
    finding_verifier: Any | None = None,
):
    """Compile the full-book, versioned and review-aware ingestion workflow."""
    dependencies = entities.IngestionDependencies(
        read_page = read_page,
        write_artifact = write_artifact,
        auditor = auditor,
        specialists = specialists,
        metadata_normalizer = metadata_normalizer,
        progress_callback = progress_callback,
        finding_verifier = finding_verifier,
    )

    workflow = StateGraph(state.IngestionRunState)
    workflow.add_node("audit_batch", partial(service.audit_batch, dependencies))
    workflow.add_node("plan_repairs", partial(service.plan_repairs, dependencies))
    workflow.add_node("repair_pages", partial(service.repair_pages, dependencies))
    workflow.add_node("normalize_metadata", partial(service.normalize_metadata, dependencies))
    workflow.add_node("publish", partial(service.publish, dependencies))
    workflow.add_edge(START, "audit_batch")
    workflow.add_edge("audit_batch", "plan_repairs")
    workflow.add_edge("plan_repairs", "repair_pages")
    workflow.add_edge("repair_pages", "normalize_metadata")
    workflow.add_edge("normalize_metadata", "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer = checkpointer)
