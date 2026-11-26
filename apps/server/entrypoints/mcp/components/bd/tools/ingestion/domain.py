"""Pure projections from persisted Agentic Ingestion manifests to MCP DTOs."""
from __future__ import annotations

from . import schemas


def run_summary(manifest: dict) -> schemas.IngestionRunSummary:
    progress = manifest.get("progress") or {}
    return schemas.IngestionRunSummary(
        book_id = manifest["book_id"],
        agentic_run_id = manifest["agentic_run_id"],
        extraction_run_id = manifest["extraction_run_id"],
        workflow_version = manifest["workflow_version"],
        status = manifest["status"],
        current_node = progress.get("current_node"),
        node_status = progress.get("node_status") or {},
        expected_page_count = manifest.get("expected_page_count", 0),
        processed_page_count = len(progress.get("processed_pages") or []),
        review_page_count = manifest.get("review_page_count", 0),
        unverified_page_count = manifest.get("unverified_page_count", 0),
        selected_page_count = manifest.get("selected_page_count"),
        excluded_page_count = manifest.get("excluded_page_count"),
        quality_status = manifest.get("quality_status"),
        replay_from_node = manifest.get("replay_from_node"),
        created_at = manifest["created_at"],
        updated_at = manifest["updated_at"],
        finished_at = manifest.get("finished_at"),
        error = manifest.get("error"),
    )


def quality_summary(manifest: dict) -> schemas.IngestionQualitySummary:
    return schemas.IngestionQualitySummary(
        book_id = manifest["book_id"],
        agentic_run_id = manifest["agentic_run_id"],
        status = manifest["status"],
        quality_status = manifest.get("quality_status"),
        expected_page_count = manifest.get("expected_page_count", 0),
        delivered_page_count = manifest.get("delivered_page_count", 0),
        discarded_blank_page_count = manifest.get("discarded_blank_page_count", 0),
        review_page_count = manifest.get("review_page_count", 0),
        unverified_page_count = manifest.get("unverified_page_count", 0),
        visual_failed_count = manifest.get("visual_failed_count", 0),
        quality_report_available = bool(
            manifest.get("quality_report_key") and manifest.get("quality_report_sha256")
        ),
        updated_at = manifest["updated_at"],
    )


def command_result(result: dict) -> schemas.IngestionCommandResult:
    return schemas.IngestionCommandResult(
        status = result["status"],
        book_id = result["book_id"],
        agentic_run_id = result["agentic_run_id"],
        extraction_run_id = result["extraction_run_id"],
        replay_from_node = result.get("replay_from_node"),
        message = result.get("message"),
    )
