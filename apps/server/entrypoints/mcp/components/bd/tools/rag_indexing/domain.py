"""Pure projections from durable RAG indexing manifests to MCP DTOs."""
from __future__ import annotations

from . import schemas


def index_summary(manifest: dict) -> schemas.RetrievalIndexSummary:
    return schemas.RetrievalIndexSummary(
        book_id = manifest["book_id"],
        extraction_run_id = manifest["extraction_run_id"],
        agentic_run_id = manifest["agentic_run_id"],
        workflow_version = manifest["workflow_version"],
        status = manifest["status"],
        current_node = manifest.get("current_node"),
        node_status = manifest.get("node_status") or {},
        attempt = manifest["attempt"],
        processed_book_revision = manifest["processed_book_revision"],
        processed_page_count = manifest["processed_page_count"],
        pin_sources_pages_completed = manifest.get("pin_sources_pages_completed", 0),
        elasticsearch_document_count = manifest.get("elasticsearch_document_count", 0),
        elasticsearch_passage_count = manifest.get("elasticsearch_passage_count", 0),
        elasticsearch_pages_completed = manifest.get("elasticsearch_pages_completed", 0),
        qdrant_point_count = manifest.get("qdrant_point_count", 0),
        qdrant_pages_completed = manifest.get("qdrant_pages_completed", 0),
        neo4j_node_count = manifest.get("neo4j_node_count", 0),
        neo4j_relationship_count = manifest.get("neo4j_relationship_count", 0),
        neo4j_pages_completed = manifest.get("neo4j_pages_completed", 0),
        neo4j_stage = manifest.get("neo4j_stage", "idle"),
        neo4j_stage_completed = manifest.get("neo4j_stage_completed", 0),
        neo4j_stage_total = manifest.get("neo4j_stage_total", 0),
        replay_from_node = manifest.get("replay_from_node"),
        cancel_requested = manifest.get("cancel_requested", False),
        created_at = manifest["created_at"],
        updated_at = manifest["updated_at"],
        finished_at = manifest.get("finished_at"),
        error = manifest.get("error"),
    )


def current_index_summary(current: dict) -> schemas.CurrentIndexSummary:
    manifest = current["index"]
    return schemas.CurrentIndexSummary(
        book_id = current["book_id"],
        extraction_run_id = current["extraction_run_id"],
        agentic_run_id = current["agentic_run_id"],
        status = "done",
        run_status = current.get("run_status", manifest["status"]),
        attempt = current.get("attempt"),
        processed_book_revision = current["processed_book_revision"],
        published_at = current.get("published_at"),
        elasticsearch_document_count = manifest.get("elasticsearch_document_count", 0),
        elasticsearch_passage_count = current.get("elasticsearch_passage_count", 0),
        qdrant_point_count = manifest.get("qdrant_point_count", 0),
        neo4j_node_count = manifest.get("neo4j_node_count", 0),
        neo4j_relationship_count = manifest.get("neo4j_relationship_count", 0),
    )


def command_result(result: dict) -> schemas.IndexCommandResult:
    return schemas.IndexCommandResult(
        status = result["status"],
        book_id = result["book_id"],
        agentic_run_id = result["agentic_run_id"],
        attempt = result["attempt"],
        replay_from_node = result.get("replay_from_node"),
        message = result.get("message"),
    )
