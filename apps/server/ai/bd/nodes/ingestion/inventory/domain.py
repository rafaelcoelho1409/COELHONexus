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
    markdown_without_images = patterns.IMAGE_PLACEHOLDER_RE.sub("", markdown_without_images)
    if patterns.PAGE_NUMBER_ONLY_RE.fullmatch(markdown_without_images):
        markdown_without_images = ""
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
    alphanumeric_chars = sum(character.isalnum() for character in markdown_without_images)
    if (
        page_kind == agents.schemas.PageKind.TEXT
        and alphanumeric_chars < params.MIN_TEXT_ALPHANUMERIC_CHARS
    ):
        issues.append(_issue(
            page_number = page_number,
            code = agents.schemas.IssueCode.NEAR_EMPTY,
            severity = agents.schemas.IssueSeverity.LOW,
            description = (
                f"Page contains only {alphanumeric_chars} alphanumeric text character(s); "
                "verify whether it is intentionally sparse."
            ),
            markdown = markdown,
            offset = 0,
            locator = "page start",
        ))

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

    dotted_leader_match = patterns.TOC_DOTTED_LEADER_RE.search(markdown)
    if dotted_leader_match:
        issues.append(_issue(
            page_number = page_number,
            code = agents.schemas.IssueCode.TOC_NOISE,
            severity = agents.schemas.IssueSeverity.LOW,
            description = "Contents-like dotted leaders may need a clearer Markdown representation.",
            markdown = markdown,
            offset = dotted_leader_match.start(),
            locator = "table-of-contents row",
            evidence_end = dotted_leader_match.end(),
        ))

    malformed_table = _first_malformed_table_row(markdown)
    if malformed_table:
        row_start, row_end = malformed_table
        issues.append(_issue(
            page_number = page_number,
            code = agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT,
            severity = agents.schemas.IssueSeverity.MEDIUM,
            description = "A Markdown table-like block has inconsistent column counts; verify its structure.",
            markdown = markdown,
            offset = row_start,
            locator = "markdown table row",
            evidence_end = row_end,
        ))

    heading_match = next((
        match for match in patterns.HEADING_RE.finditer(markdown)
        if _has_suspicious_heading_fragment(match.group(1))
    ), None)
    if heading_match:
        heading_start = heading_match.start(1)
        heading_end = heading_match.end(1)
        issues.append(_issue(
            page_number = page_number,
            code = agents.schemas.IssueCode.SPLIT_WORD,
            severity = agents.schemas.IssueSeverity.MEDIUM,
            description = "An uppercase heading contains suspiciously separated word fragments; verify against the source.",
            markdown = markdown,
            offset = heading_start,
            locator = "heading character offset",
            evidence_end = heading_end,
        ))

    return agents.schemas.PageAssessment(
        page_number = page_number,
        page_kind = page_kind,
        confidence = 1.0,
        issues = issues,
    )


def merge_issues(
    deterministic: list[agents.schemas.PageIssue],
    proposed: list[agents.schemas.PageIssue],
    markdown: str,
) -> list[agents.schemas.PageIssue]:
    """Combine deterministic and auditor findings without double-counting one defect.

    An auditor finding is a duplicate when a kept finding has the same code and its evidence overlaps in the page;
    contents-noise findings collapse to one page-level issue that carries every distinct excerpt."""
    merged = list(deterministic)
    for issue in proposed:
        if any(kept.issue_id == issue.issue_id for kept in merged):
            continue
        same_code = [kept for kept in merged if kept.code == issue.code]
        if issue.code == agents.schemas.IssueCode.TOC_NOISE and same_code:
            kept = same_code[0]
            known = {evidence.excerpt for evidence in kept.evidence}
            extra = [evidence for evidence in issue.evidence if evidence.excerpt not in known]
            if extra:
                merged[merged.index(kept)] = kept.model_copy(update = {"evidence": kept.evidence + extra})
            continue
        if any(_evidence_overlaps(kept, issue, markdown) for kept in same_code):
            continue
        merged.append(issue)
    return merged


def _evidence_overlaps(left: agents.schemas.PageIssue, right: agents.schemas.PageIssue, markdown: str) -> bool:
    for first in left.evidence:
        first_start = markdown.find(first.excerpt)
        for second in right.evidence:
            second_start = markdown.find(second.excerpt)
            if first_start < 0 or second_start < 0:
                if first.excerpt in second.excerpt or second.excerpt in first.excerpt:
                    return True
                continue
            if (
                first_start < second_start + len(second.excerpt)
                and second_start < first_start + len(first.excerpt)
            ):
                return True
    return False


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
    evidence_end: int | None = None,
) -> agents.schemas.PageIssue:
    radius = params.EVIDENCE_CONTEXT_CHARS // 2
    start = max(0, offset - radius)
    end = min(len(markdown), max(offset + radius, evidence_end or offset))
    if evidence_end is not None and evidence_end - start > params.MAX_DETERMINISTIC_EVIDENCE_CHARS:
        end = start + params.MAX_DETERMINISTIC_EVIDENCE_CHARS
    excerpt = markdown[start:end]
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


def _has_suspicious_heading_fragment(heading: str) -> bool:
    standalone_letters = len(patterns.STANDALONE_LETTER_RE.findall(heading))
    for match in patterns.HEADING_FRAGMENT_RE.finditer(heading):
        if heading[:match.start()].strip() == "A":
            continue
        # "A"/"I" are real words, so a lone one is only suspicious when the heading is fragmented elsewhere.
        if heading[match.start()] in "AI" and standalone_letters < 2:
            continue
        return True
    return False


def _first_malformed_table_row(markdown: str) -> tuple[int, int] | None:
    lines = markdown.splitlines(keepends = True)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    for index, line in enumerate(lines):
        separator_cells = _table_cells(line)
        if len(separator_cells) < 2 or not all(
            patterns.TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in separator_cells
        ):
            continue

        expected_columns = len(separator_cells)
        header_index = index - 1
        if header_index < 0 or len(_table_cells(lines[header_index])) != expected_columns:
            malformed_index = header_index if header_index >= 0 and "|" in lines[header_index] else index
            return offsets[malformed_index], offsets[malformed_index] + len(lines[malformed_index].rstrip("\r\n"))

        for row_index in range(index + 1, len(lines)):
            row = lines[row_index]
            if not row.strip() or "|" not in row:
                break
            if len(_table_cells(row)) != expected_columns:
                return offsets[row_index], offsets[row_index] + len(row.rstrip("\r\n"))

    return None


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]
