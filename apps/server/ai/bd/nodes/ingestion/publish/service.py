"""Quality reporting and manifest-last publication for an agentic ingestion run."""
from __future__ import annotations
import domains
from .... import agents, graphs

import hashlib
import inspect
import json
import time
from collections import Counter
from collections.abc import Callable
from typing import Any


ArtifactWriter = Callable[[str, str, str], Any]


async def publish(
    state: graphs.ingestion.state.IngestionRunState,
    write_artifact: ArtifactWriter,
) -> dict:
    """Write the quality report and the completed run manifest as the final object write."""
    page_results = state.get("page_results")
    expected_page_count = state.get("expected_page_count")
    if not isinstance(page_results, list) or len(page_results) != expected_page_count:
        raise ValueError("cannot publish an incomplete page result set")
    if [page.get("page") for page in page_results] != list(range(1, expected_page_count + 1)):
        raise ValueError("page results must cover every physical page in order")
    if any(page.get("status") not in {"done", "review_required"} for page in page_results):
        raise ValueError("cannot publish a page with a failed or pending status")
    if not state.get("book_metadata_sha256"):
        raise ValueError("book metadata must be persisted before run publication")

    assessments = [agents.schemas.PageAssessment.model_validate(page["assessment"]) for page in page_results]
    issue_counts = Counter(issue.code.value for assessment in assessments for issue in assessment.issues)
    page_kinds = Counter(assessment.page_kind.value for assessment in assessments)
    review_pages = [page["page"] for page in page_results if page["status"] == "review_required"]
    delivered_page_numbers = [
        page["page"] for page in page_results
        if page.get("delivery_disposition", "include") != "discarded_blank"
    ]
    discarded_blank_pages = [
        page["page"] for page in page_results
        if page.get("delivery_disposition") == "discarded_blank"
    ]
    audit_fallback_pages = [
        page["page"] for page in state["page_audits"]
        if page.get("structured_output_fallback") is True
    ]
    audit_warning_count = sum(len(page.get("warnings", [])) for page in state["page_audits"])
    repair_context_chars = sum(page.get("repair_context_chars", 0) for page in page_results)
    markdown_chars_by_page = {
        page["page"]: page["markdown_chars"] for page in state["page_audits"]
    }
    unbounded_repair_context_chars = sum(
        markdown_chars_by_page.get(page["page"], 0) * page.get("repair_call_count", 0)
        for page in page_results
    )
    # Audit warnings are informational (a discarded excerpt or label conflict was already handled); only an unverified
    # page, an unresolved issue or unreviewed metadata keeps the run from being fully accepted.
    quality_status = (
        "complete_with_review"
        if (
            review_pages
            or state["book_metadata"].get("review_required")
            or audit_fallback_pages
        )
        else "complete"
    )
    fallback_page_set = set(audit_fallback_pages)
    review_reasons = Counter()
    mechanical_fixes = Counter()
    for page in page_results:
        mechanical_fixes.update(page.get("mechanical_fix_counts", {}))
        if page["status"] != "review_required":
            continue
        if page["review_required_issue_ids"]:
            review_reasons["unresolved_issues"] += 1
        if page.get("error"):
            review_reasons["repair_errors"] += 1
        if page["page"] in fallback_page_set:
            review_reasons["degraded_audit"] += 1
    quality_report = {
        "book_id": state["book_id"],
        "extraction_run_id": state["extraction_run_id"],
        "agentic_run_id": state["agentic_run_id"],
        "workflow_version": state["workflow_version"],
        "pages_total": expected_page_count,
        "pages_completed": len(page_results),
        "delivered_page_numbers": delivered_page_numbers,
        "delivered_page_count": len(delivered_page_numbers),
        "discarded_blank_pages": discarded_blank_pages,
        "discarded_blank_page_count": len(discarded_blank_pages),
        "pages_requiring_review": review_pages,
        "review_reasons": dict(sorted(review_reasons.items())),
        "mechanical_fixes": dict(sorted(mechanical_fixes.items())),
        "pages_with_audit_warnings": [
            page["page"] for page in state["page_audits"] if page.get("warnings")
        ],
        "audit_warning_count": audit_warning_count,
        "structured_output_fallback_pages": audit_fallback_pages,
        "structured_output_fallback_count": len(audit_fallback_pages),
        "deterministic_only_audit_pages": [
            page["page"] for page in state["page_audits"]
            if page.get("audit_mode") == "deterministic_only"
        ],
        "page_kinds": dict(sorted(page_kinds.items())),
        "blank_pages": [assessment.page_number for assessment in assessments if assessment.page_kind.value == "blank"],
        "image_only_pages": [
            assessment.page_number
            for assessment in assessments
            if assessment.page_kind.value == "image_only"
        ],
        "issues_by_code": dict(sorted(issue_counts.items())),
        "proposals_total": sum(page["patch_proposal_count"] for page in page_results),
        "patches_auto_applied": sum(len(page["auto_applied_issue_ids"]) for page in page_results),
        "unresolved_issue_count": sum(len(page["review_required_issue_ids"]) for page in page_results),
        "repair_context_chars": repair_context_chars,
        "estimated_unbounded_repair_context_chars": unbounded_repair_context_chars,
        "estimated_repair_context_chars_saved": max(0, unbounded_repair_context_chars - repair_context_chars),
        "media_pages": [
            page["page"] for page in page_results
            if page["assessment"].get("page_kind") in {"mixed", "image_only"}
        ],
        "book_metadata_review_required": state["book_metadata"].get("review_required", True),
        "status": quality_status,
    }
    report_content = _json(quality_report)
    report_sha256 = hashlib.sha256(report_content.encode("utf-8")).hexdigest()
    report_key = domains.bd.ingestion.storage.keys.agentic_quality_report_key(
        state["book_id"], state["agentic_run_id"],
    )
    await _write(write_artifact, report_key, report_content, "application/json")

    page_artifacts = [domains.bd.ingestion.ai.schemas.IngestionPageArtifact(
        page_number = page["page"],
        source_key = next(ref["source_key"] for ref in state["page_refs"] if ref["page"] == page["page"]),
        source_sha256 = page["source_sha256"],
        normalized_key = page["normalized_key"],
        assessment_key = page["assessment_key"],
        patch_proposals_key = page["patch_proposals_key"],
        normalized_sha256 = page["normalized_sha256"],
        assessment_sha256 = page["assessment_sha256"],
        patch_proposals_sha256 = page["patch_proposals_sha256"],
        status = domains.bd.ingestion.ai.schemas.AgenticPageStatus(page["status"]),
        delivery_disposition = page.get("delivery_disposition", "include"),
    ) for page in page_results]
    finished_at = time.time()
    manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest(
        workflow_version = state["workflow_version"],
        agentic_run_id = state["agentic_run_id"],
        book_id = state["book_id"],
        extraction_run_id = state["extraction_run_id"],
        source_manifest_key = state["source_manifest_key"],
        source_manifest_sha256 = state["source_manifest_sha256"],
        status = domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE,
        expected_page_count = expected_page_count,
        pages = page_artifacts,
        book_metadata_key = domains.bd.ingestion.storage.keys.agentic_book_metadata_key(
            state["book_id"], state["agentic_run_id"],
        ),
        book_metadata_sha256 = state["book_metadata_sha256"],
        quality_report_key = report_key,
        quality_report_sha256 = report_sha256,
        quality_status = quality_status,
        delivered_page_count = len(delivered_page_numbers),
        discarded_blank_page_count = len(discarded_blank_pages),
        review_page_count = len(review_pages),
        created_at = state["run_created_at"],
        updated_at = finished_at,
        finished_at = finished_at,
    )
    manifest_content = _json(manifest.model_dump(mode = "json"))
    manifest_key = domains.bd.ingestion.storage.keys.agentic_manifest_key(
        state["book_id"], state["agentic_run_id"],
    )
    await _write(write_artifact, manifest_key, manifest_content, "application/json")
    return {
        "quality_report": quality_report,
        "quality_report_sha256": report_sha256,
        "status": "complete",
        "quality_status": quality_status,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


async def _write(writer: ArtifactWriter, key: str, content: str, content_type: str) -> None:
    result = writer(key, content, content_type)
    if inspect.isawaitable(result):
        await result
