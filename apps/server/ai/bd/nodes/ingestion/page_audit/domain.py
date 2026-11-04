"""Validation and prompt-payload construction for page audits."""
from __future__ import annotations
from . import params
from .... import agents, graphs

import json
from typing import Any


def validate_request(
    state: graphs.ingestion.state.PageAuditState,
) -> tuple[str, str, int, str, int]:
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    extraction_status = state.get("extraction_status")
    page_ref = state.get("page_ref")

    if not _is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if not _is_safe_segment(extraction_run_id):
        raise ValueError("extraction_run_id must be a safe path segment")
    if extraction_status != "done":
        raise ValueError("only completed extraction runs can be audited")
    if not isinstance(page_ref, dict):
        raise ValueError("page_ref must be a manifest page object")

    page_number = page_ref.get("page")
    object_key = page_ref.get("key")
    pictures = page_ref.get("pictures", 0)
    if type(page_number) is not int or page_number <= 0:
        raise ValueError("page number must be a positive integer")
    if not isinstance(object_key, str) or not object_key:
        raise ValueError("page reference has no object key")
    if type(pictures) is not int or pictures < 0:
        raise ValueError("page reference has an invalid pictures count")

    expected_key = (
        f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/"
        f"{page_number:04d}.md"
    )
    if object_key != expected_key:
        raise ValueError("page reference does not use its canonical raw object key")

    return book_id, extraction_run_id, page_number, object_key, pictures


def user_payload(page_number: int, pictures: int, markdown: str) -> str:
    return json.dumps({
        "task": "Assess extraction quality for this one physical page.",
        "page_number": page_number,
        "extracted_picture_count": pictures,
        "markdown": markdown,
    }, ensure_ascii = False)


def validate_assessment(
    value: Any,
    *,
    page_number: int,
    pictures: int,
    markdown: str,
) -> agents.schemas.PageAssessment:
    if isinstance(value, agents.schemas.PageAssessment):
        value = value.model_dump(mode = "python")
    assessment = agents.schemas.PageAssessment.model_validate(value)

    if assessment.page_number != page_number:
        raise ValueError("assessment page_number does not match the requested page")
    if len(assessment.issues) > params.MAX_ISSUES_PER_PAGE:
        raise ValueError("assessment exceeds the maximum issues per page")

    media_kinds = {agents.schemas.PageKind.IMAGE_ONLY, agents.schemas.PageKind.MIXED}
    text_kinds = {agents.schemas.PageKind.TEXT, agents.schemas.PageKind.BLANK}
    if pictures > 0 and assessment.page_kind in text_kinds:
        raise ValueError("assessment contradicts the extracted-picture count")
    if pictures == 0 and assessment.page_kind in media_kinds:
        raise ValueError("assessment reports media that the extraction did not find")

    for issue in assessment.issues:
        for evidence in issue.evidence:
            if evidence.source != agents.schemas.EvidenceSource.PAGE_MARKDOWN:
                raise ValueError("page-audit evidence must come from the supplied Markdown")
            if evidence.page_number != page_number:
                raise ValueError("evidence page_number does not match the requested page")
            if len(evidence.excerpt) > params.MAX_EVIDENCE_CHARS:
                raise ValueError("evidence excerpt exceeds the maximum length")
            if evidence.excerpt not in markdown:
                raise ValueError("evidence excerpt is not an exact substring of the source page")

    return assessment


def sanitize_assessment(
    value: Any,
    *,
    page_number: int,
    pictures: int,
    markdown: str,
) -> tuple[agents.schemas.PageAssessment, list[str]]:
    """Keep only auditor findings whose evidence can be verified against this page."""
    assessment = agents.schemas.PageAssessment.model_validate(value)
    if assessment.page_number != page_number:
        raise ValueError("assessment page_number does not match the requested page")
    if len(assessment.issues) > params.MAX_ISSUES_PER_PAGE:
        raise ValueError("assessment exceeds the maximum issues per page")

    media_kinds = {agents.schemas.PageKind.IMAGE_ONLY, agents.schemas.PageKind.MIXED}
    text_kinds = {agents.schemas.PageKind.TEXT, agents.schemas.PageKind.BLANK}
    warnings = []
    if pictures > 0 and assessment.page_kind in text_kinds:
        warnings.append("The auditor page-kind label conflicted with extracted media; the deterministic label was kept.")
    if pictures == 0 and assessment.page_kind in media_kinds:
        warnings.append("The auditor reported media that extraction did not find; the deterministic label was kept.")

    valid_issues = []
    invalid_evidence_count = 0
    dropped_issue_count = 0
    for issue in assessment.issues:
        verified_evidence = [
            evidence
            for evidence in issue.evidence
            if evidence.source == agents.schemas.EvidenceSource.PAGE_MARKDOWN
            and evidence.page_number == page_number
            and len(evidence.excerpt) <= params.MAX_EVIDENCE_CHARS
            and evidence.excerpt in markdown
        ]
        invalid_evidence_count += len(issue.evidence) - len(verified_evidence)
        if not verified_evidence:
            dropped_issue_count += 1
            continue
        valid_issues.append(issue.model_copy(update = {"evidence": verified_evidence}))

    if invalid_evidence_count:
        warnings.append(
            f"Discarded {invalid_evidence_count} auditor evidence excerpt(s) that were not exact source text."
        )
    if dropped_issue_count:
        warnings.append(f"Discarded {dropped_issue_count} auditor finding(s) without verifiable evidence.")

    return assessment.model_copy(update = {"issues": valid_issues}), warnings


def _is_safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )
