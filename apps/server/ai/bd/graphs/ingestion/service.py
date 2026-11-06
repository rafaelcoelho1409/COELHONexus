"""The five nodes of the ingestion graph: each runs one stage under progress reporting, from the run's dependencies."""
from __future__ import annotations
from . import entities, state
from ... import graphs, nodes


async def audit_batch(
    dependencies: entities.IngestionDependencies, 
    graph_state: state.IngestionRunState) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "audit_batch",
        lambda: nodes.ingestion.audit_batch.node.audit_pages(
            graph_state,
            dependencies.read_page,
            dependencies.auditor,
            dependencies.progress_callback,
            dependencies.finding_verifier,
        ),
    )


async def plan_repairs(
    dependencies: entities.IngestionDependencies,
    graph_state: state.IngestionRunState) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "plan_repairs",
        lambda: nodes.ingestion.plan_repairs.node.plan_repairs(graph_state),
    )


async def repair_pages(
    dependencies: entities.IngestionDependencies, 
    graph_state: state.IngestionRunState) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "repair_pages",
        lambda: nodes.ingestion.repair_pages.node.repair_pages(
            graph_state,
            dependencies.read_page,
            dependencies.write_artifact,
            dependencies.specialists,
            dependencies.progress_callback,
        ),
    )


async def normalize_metadata(
    dependencies: entities.IngestionDependencies, 
    graph_state: state.IngestionRunState) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "normalize_metadata",
        lambda: nodes.ingestion.metadata_normalization.node.normalize_metadata(
            graph_state,
            dependencies.read_page,
            dependencies.write_artifact,
            dependencies.metadata_normalizer,
        ),
    )


async def publish(
    dependencies: entities.IngestionDependencies, 
    graph_state: state.IngestionRunState) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "publish",
        lambda: nodes.ingestion.publish.node.publish(
            graph_state,
            dependencies.write_artifact,
            dependencies.read_page,
        ),
    )
