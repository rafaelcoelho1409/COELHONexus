"""Pure validation and quality-signal logic for page inventory."""
from __future__ import annotations
from . import params, patterns
from .... import graphs, agents

import unicodedata
from collections import Counter
from typing import Any


def validate_request(
    state: graphs.ingestion.state.IngestionInventoryState,
) -> tuple[str, str, list[graphs.ingestion.state.PageReference]]:
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    extraction_status = state.get("extraction_status")
    expected_page_count = state.get("expected_page_count")
    page_refs = state.get("page_refs")

    if not isinstance(book_id, str) or not book_id.strip():
        raise ValueError("book_id is required")
    if not isinstance(extraction_run_id, str) or not extraction_run_id.strip():
        raise ValueError("extraction_run_id is required")
    if extraction_status != "done":
        raise ValueError("only completed extraction runs can be inventoried")
    if not _is_safe_segment(book_id) or not _is_safe_segment(extraction_run_id):
        raise ValueError("book_id and extraction_run_id must be safe path segments")
    if type(expected_page_count) is not int or expected_page_count <= 0:
        raise ValueError("expected_page_count must be a positive integer")
    if not isinstance(page_refs, list) or len(page_refs) != expected_page_count:
        raise ValueError("page_refs must cover the expected physical page count")

    normalized_refs: list[graphs.ingestion.state.PageReference] = []
    for expected_page, page_ref in enumerate(page_refs, start = 1):
        if not isinstance(page_ref, dict):
            raise ValueError("each page reference must be a manifest object")
        page_number = page_ref.get("page")
        object_key = page_ref.get("key")
        pictures = page_ref.get("pictures", 0)
        if type(page_number) is not int or page_number != expected_page:
            raise ValueError("page_refs must list every page once in physical page order")
        if not isinstance(object_key, str) or not object_key:
            raise ValueError(f"page {page_number} has no object key")
        if type(pictures) is not int or pictures < 0:
            raise ValueError(f"page {page_number} has an invalid pictures count")

        expected_key = (
            f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/"
            f"{page_number:04d}.md"
        )
        if object_key != expected_key:
            raise ValueError(f"page {page_number} does not use its canonical raw object key")
        normalized_refs.append({"page": page_number, "key": object_key, "pictures": pictures})

    return book_id, extraction_run_id, normalized_refs


def assess_page(
    page_number: int,
    markdown: str,
    pictures: int,
) -> agents.schemas.PageAssessment:
    markdown_without_images = markdown
    if pictures > 0:
        markdown_without_images = patterns.MARKDOWN_IMAGE_RE.sub("", markdown_without_images)
        markdown_without_images = patterns.HTML_IMAGE_RE.sub("", markdown_without_images)
    has_text = bool(markdown_without_images.strip())
    has_media = pictures > 0

    if has_media and not has_text:
        page_kind = agents.schemas.PageKind.IMAGE_ONLY
    elif has_media:
        page_kind = agents.schemas.PageKind.MIXED
    elif has_text:
        page_kind = agents.schemas.PageKind.TEXT
    else:
        page_kind = agents.schemas.PageKind.BLANK

    issues: list[agents.schemas.PageIssue] = []
    control_offsets = [
        index
        for index, character in enumerate(markdown)
        if unicodedata.category(character) == "Cc" and character not in "\t\n\r"
    ]
    if control_offsets:
        issues.append(_issue(
            page_number = page_number,
            code = agents.schemas.IssueCode.CONTROL_CHARACTERS,
            severity = agents.schemas.IssueSeverity.MEDIUM,
            description = f"Found {len(control_offsets)} non-whitespace control character(s).",
            markdown = markdown,
            offset = control_offsets[0],
            locator = "character offset",
        ))

    soft_hyphen_offsets = [index for index, character in enumerate(markdown) if character == "\u00ad"]
    if soft_hyphen_offsets:
        issues.append(_issue(
            page_number = page_number,
            code = agents.schemas.IssueCode.SOFT_HYPHEN,
            severity = agents.schemas.IssueSeverity.LOW,
            description = f"Found {len(soft_hyphen_offsets)} soft hyphen(s); review before normalization.",
            markdown = markdown,
            offset = soft_hyphen_offsets[0],
            locator = "character offset",
        ))

    return agents.schemas.PageAssessment(
        page_number = page_number,
        page_kind = page_kind,
        confidence = 1.0,
        issues = issues,
    )


def inventory_summary(
    pages: list[graphs.ingestion.state.PageInventoryResult],
) -> dict[str, Any]:
    page_kinds = Counter(page["assessment"]["page_kind"] for page in pages)
    issue_counts = Counter(
        issue["code"]
        for page in pages
        for issue in page["assessment"]["issues"]
    )
    return {
        "pages_total": len(pages),
        "markdown_characters": sum(page["markdown_chars"] for page in pages),
        "media_total": sum(page["pictures"] for page in pages),
        "page_kinds": dict(sorted(page_kinds.items())),
        "blank_pages": [
            page["page"] for page in pages
            if page["assessment"]["page_kind"] == agents.schemas.PageKind.BLANK.value
        ],
        "image_only_pages": [
            page["page"] for page in pages
            if page["assessment"]["page_kind"] == agents.schemas.PageKind.IMAGE_ONLY.value
        ],
        "issue_counts": dict(sorted(issue_counts.items())),
    }


def _issue(
    *,
    page_number: int,
    code: agents.schemas.IssueCode,
    severity: agents.schemas.IssueSeverity,
    description: str,
    markdown: str,
    offset: int,
    locator: str,
) -> agents.schemas.PageIssue:
    radius = params.EVIDENCE_CONTEXT_CHARS // 2
    excerpt = markdown[max(0, offset - radius):min(len(markdown), offset + radius)]
    return agents.schemas.PageIssue(
        issue_id = f"p{page_number:04d}-{code.value}",
        code = code,
        severity = severity,
        description = description,
        confidence = 1.0,
        evidence = [agents.schemas.EvidenceRef(
            source = agents.schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = excerpt,
            page_number = page_number,
            locator = f"{locator} {offset}",
        )],
    )


def _is_safe_segment(value: str) -> bool:
    return all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )
