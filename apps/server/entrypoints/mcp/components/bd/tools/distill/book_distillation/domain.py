"""Pure projections and query normalization for BD Distill MCP contracts."""
from __future__ import annotations
from . import schemas


def run_summary(manifest: dict) -> schemas.DistillRunSummary:
    progress = manifest.get("progress") or {}
    return schemas.DistillRunSummary(
        book_id = manifest["book_id"],
        run_id = manifest["run_id"],
        extraction_run_id = manifest["extraction_run_id"],
        ingestion_ai_run_id = manifest["ingestion_ai_run_id"],
        workflow_version = manifest["workflow_version"],
        prompt_version = manifest["prompt_version"],
        status = manifest["status"],
        current_node = progress.get("current_node"),
        current_task = progress.get("current_task"),
        node_status = progress.get("node_status") or {},
        node_progress = progress.get("node_progress") or {},
        completed_extractions = progress.get("completed_extractions", 0),
        completed_units = progress.get("completed_units", 0),
        total_units = progress.get("total_units", 0),
        completed_chapter_guides = progress.get("completed_chapter_guides", 0),
        completed_chapter_visuals = progress.get("completed_chapter_visuals", 0),
        completed_chapters = progress.get("completed_chapters", 0),
        total_chapters = progress.get("total_chapters", 0),
        research_enabled = manifest.get("research_enabled", False),
        report_available = bool(manifest.get("report_key") and manifest.get("report_sha256")),
        review_required = manifest.get("review_required", True),
        created_at = manifest["created_at"],
        updated_at = manifest["updated_at"],
        finished_at = manifest.get("finished_at"),
        error = manifest.get("error"),
    )


def command_result(result: dict) -> schemas.DistillCommandResult:
    return schemas.DistillCommandResult(
        status = result["status"],
        book_id = result["book_id"],
        run_id = result["run_id"],
        extraction_run_id = result["extraction_run_id"],
        ingestion_ai_run_id = result["ingestion_ai_run_id"],
        replay_from_node = result.get("replay_from_node"),
        message = result.get("error"),
    )


def normalize_query(value: str, max_chars: int) -> str:
    """Normalize and bound a retrieval query before dispatch."""
    return " ".join(value.split())[:max_chars].strip()
