"""Pure validation and quality-signal logic for page inventory."""
from __future__ import annotations
import ai
from . import params, patterns

import unicodedata


def assess_page(
    page_number: int,
    markdown: str,
    pictures: int,
) -> ai.bd.agents.schemas.PageAssessment:
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
        page_kind = ai.bd.agents.schemas.PageKind.IMAGE_ONLY
    elif has_media:
        page_kind = ai.bd.agents.schemas.PageKind.MIXED
    elif has_text:
        page_kind = ai.bd.agents.schemas.PageKind.TEXT
    else:
        page_kind = ai.bd.agents.schemas.PageKind.BLANK

    issues: list[ai.bd.agents.schemas.PageIssue] = []
    alphanumeric_chars = sum(character.isalnum() for character in markdown_without_images)
    if (
        page_kind == ai.bd.agents.schemas.PageKind.TEXT
        and alphanumeric_chars < params.MIN_TEXT_ALPHANUMERIC_CHARS
    ):
        issues.append(_issue(
            page_number = page_number,
            code = ai.bd.agents.schemas.IssueCode.NEAR_EMPTY,
            severity = ai.bd.agents.schemas.IssueSeverity.LOW,
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
            code = ai.bd.agents.schemas.IssueCode.CONTROL_CHARACTERS,
            severity = ai.bd.agents.schemas.IssueSeverity.MEDIUM,
            description = f"Found {len(control_offsets)} non-whitespace control character(s).",
            markdown = markdown,
            offset = control_offsets[0],
            locator = "character offset",
        ))

    soft_hyphen_offsets = [index for index, character in enumerate(markdown) if character == "­"]
    if soft_hyphen_offsets:
        issues.append(_issue(
            page_number = page_number,
            code = ai.bd.agents.schemas.IssueCode.SOFT_HYPHEN,
            severity = ai.bd.agents.schemas.IssueSeverity.LOW,
            description = f"Found {len(soft_hyphen_offsets)} soft hyphen(s); review before normalization.",
            markdown = markdown,
            offset = soft_hyphen_offsets[0],
            locator = "character offset",
        ))

    dotted_leader_match = patterns.TOC_DOTTED_LEADER_RE.search(markdown)
    if dotted_leader_match:
        issues.append(_issue(
            page_number = page_number,
            code = ai.bd.agents.schemas.IssueCode.TOC_NOISE,
            severity = ai.bd.agents.schemas.IssueSeverity.LOW,
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
            code = ai.bd.agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT,
            severity = ai.bd.agents.schemas.IssueSeverity.MEDIUM,
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
            code = ai.bd.agents.schemas.IssueCode.SPLIT_WORD,
            severity = ai.bd.agents.schemas.IssueSeverity.MEDIUM,
            description = "An uppercase heading contains suspiciously separated word fragments; verify against the source.",
            markdown = markdown,
            offset = heading_start,
            locator = "heading character offset",
            evidence_end = heading_end,
        ))

    return ai.bd.agents.schemas.PageAssessment(
        page_number = page_number,
        page_kind = page_kind,
        confidence = 1.0,
        issues = issues,
    )


def merge_issues(
    deterministic: list[ai.bd.agents.schemas.PageIssue],
    proposed: list[ai.bd.agents.schemas.PageIssue],
    markdown: str,
) -> list[ai.bd.agents.schemas.PageIssue]:
    """Combine deterministic and auditor findings without double-counting one defect.

    An auditor finding is a duplicate when a kept finding has the same code and its evidence overlaps in the page;
    contents-noise findings collapse to one page-level issue that carries every distinct excerpt."""
    merged = list(deterministic)
    for issue in proposed:
        if any(kept.issue_id == issue.issue_id for kept in merged):
            continue
        same_code = [kept for kept in merged if kept.code == issue.code]
        if issue.code == ai.bd.agents.schemas.IssueCode.TOC_NOISE and same_code:
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


def _evidence_overlaps(
    left: ai.bd.agents.schemas.PageIssue,
    right: ai.bd.agents.schemas.PageIssue,
    markdown: str,
) -> bool:
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


def effective_length(markdown: str) -> int:
    """Page size as the models see it: a padded table's space runs cost a token each, not a token per space."""
    return len(patterns.SPACE_RUN_RE.sub(" ", markdown))


def build_repair_context(
    markdown: str,
    issues: list[ai.bd.agents.schemas.PageIssue],
    *,
    per_issue_chars: int,
    max_chars: int,
) -> str:
    """Return source-exact windows around issue evidence, bounded before model invocation."""
    windows: list[tuple[int, int]] = []
    for issue in issues:
        for evidence in issue.evidence:
            evidence_start = markdown.find(evidence.excerpt)
            if evidence_start < 0:
                continue
            evidence_end = evidence_start + len(evidence.excerpt)
            budget = max(per_issue_chars, len(evidence.excerpt))
            radius = max(0, (budget - len(evidence.excerpt)) // 2)
            start = max(0, evidence_start - radius)
            end = min(len(markdown), evidence_end + radius)
            windows.append((start, end))

    if not windows:
        return ""

    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    excerpts: list[str] = []
    remaining = max_chars
    for start, end in merged:
        if remaining <= 0:
            break
        excerpt = markdown[start:min(end, start + remaining)]
        if excerpt:
            excerpts.append(excerpt)
            remaining -= len(excerpt)
    return "\n…\n".join(excerpts)


def _issue(
    *,
    page_number: int,
    code: ai.bd.agents.schemas.IssueCode,
    severity: ai.bd.agents.schemas.IssueSeverity,
    description: str,
    markdown: str,
    offset: int,
    locator: str,
    evidence_end: int | None = None,
) -> ai.bd.agents.schemas.PageIssue:
    radius = params.EVIDENCE_CONTEXT_CHARS // 2
    start = max(0, offset - radius)
    end = min(len(markdown), max(offset + radius, evidence_end or offset))
    if evidence_end is not None and evidence_end - start > params.MAX_DETERMINISTIC_EVIDENCE_CHARS:
        end = start + params.MAX_DETERMINISTIC_EVIDENCE_CHARS
    excerpt = markdown[start:end]
    return ai.bd.agents.schemas.PageIssue(
        issue_id = f"p{page_number:04d}-{code.value}",
        code = code,
        severity = severity,
        description = description,
        confidence = 1.0,
        evidence = [ai.bd.agents.schemas.EvidenceRef(
            source = ai.bd.agents.schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = excerpt,
            page_number = page_number,
            locator = f"{locator} {offset}",
        )],
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
