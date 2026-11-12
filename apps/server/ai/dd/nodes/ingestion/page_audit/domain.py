"""Plain scans of an extracted page, validation and prompt-payload construction for documentation page audits."""
from __future__ import annotations
from . import params, patterns
from .... import agents

import json
import re
from typing import Any


def user_payload(
    framework_slug: str,
    page_index: int,
    page_url: str,
    page_title: str,
    markdown: str,
    candidates: list[dict[str, Any]],
) -> str:
    return json.dumps({
        "task": "Assess normalization quality for this one documentation page. `candidates` are leads from simple scans: judge each one, they are not findings.",
        "framework_slug": framework_slug,
        "page_index": page_index,
        "page_url": page_url,
        "page_title": page_title,
        "candidates": candidates,
        "markdown": markdown,
    }, ensure_ascii = False)


def is_repeat(markdown: str, evidence: str, other_evidence: str) -> bool:
    """Whether `evidence` really is repeated on the page: it occurs at least twice, or `other_evidence` is the same text (whitespace aside)
    in a different place. A model that claims a repeat it cannot point to has not found one."""
    first = evidence.strip()
    if not first:
        return False
    if markdown.count(first) >= 2:
        return True
    second = other_evidence.strip()
    if not second or len(second) > params.MAX_EVIDENCE_CHARS or " ".join(first.split()) != " ".join(second.split()):
        return False
    starts = [match.start() for match in re.finditer(re.escape(first), markdown)]
    others = [match.start() for match in re.finditer(re.escape(second), markdown)]
    return any(abs(start - other) >= min(len(first), len(second)) for start in starts for other in others)


def evidence_is_only_in_code_fences(markdown: str, evidence: str) -> bool:
    """Whether every exact occurrence of an issue's evidence is inside a fenced code block."""
    if not evidence:
        return False

    spans: list[tuple[int, int]] = []
    opening: tuple[str, int, int] | None = None
    offset = 0
    for raw_line in markdown.splitlines(keepends = True):
        line = raw_line.rstrip("\r\n")
        stripped = line.lstrip(" \t")
        marker_match = patterns.FENCE_RE.match(stripped)
        if opening is None:
            if marker_match is not None:
                marker = marker_match.group(1)
                opening = (marker[0], len(marker), offset)
        elif marker_match is not None:
            marker = marker_match.group(1)
            rest = stripped[len(marker):]
            if marker[0] == opening[0] and len(marker) >= opening[1] and not rest.strip():
                spans.append((opening[2], offset + len(raw_line)))
                opening = None
        offset += len(raw_line)

    if opening is not None:
        spans.append((opening[2], len(markdown)))

    starts = [match.start() for match in re.finditer(re.escape(evidence), markdown)]
    return bool(starts) and all(
        any(start <= occurrence and occurrence + len(evidence) <= end for start, end in spans)
        for occurrence in starts
    )


def sanitize_assessment(
    value: object,
    *,
    markdown: str,
) -> tuple[agents.schemas.PageAssessment, list[str]]:
    """Keep only auditor findings with evidence present in the source Markdown, one per category and evidence."""
    assessment = agents.schemas.PageAssessment.model_validate(value)
    if len(assessment.issues) > params.MAX_ISSUES_PER_PAGE:
        raise ValueError("assessment exceeds the maximum issues per page")

    accepted: dict[tuple[str, str], agents.schemas.PageIssue] = {}
    warnings = []
    for issue in assessment.issues:
        if len(issue.evidence) > params.MAX_EVIDENCE_CHARS or issue.evidence not in markdown:
            warnings.append(
                f"Discarded finding {issue.issue_id}: evidence was not a bounded exact source substring."
            )
            continue
        if evidence_is_only_in_code_fences(markdown, issue.evidence):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence is inside a fenced code example, not page markup."
            )
            continue
        description = issue.description.casefold()
        if (
            issue.category == "markdown_formatting"
            and agents.ingestion.page_auditor.patterns.contains_valid_escaped_ampersand(issue.evidence)
            and "ampersand" in description
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: backslash-escaped ampersands are valid Markdown punctuation escapes."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and agents.ingestion.page_auditor.patterns.evidence_is_valid_atx_heading(markdown, issue.evidence)
            and (
                "colon" in description
                or ("underscore" in description and agents.ingestion.page_auditor.patterns.contains_escaped_identifier(issue.evidence))
            )
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the cited punctuation is part of a valid ATX heading."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and agents.ingestion.page_auditor.patterns.is_valid_blockquote(issue.evidence)
            and "blockquote" in description
            and any(term in description for term in ("heading", "tagline", "unconventional", "descriptive statement"))
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: valid blockquote use is a source-style choice, not a Markdown defect."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and "empty row" in description
            and not any(re.fullmatch(r"\s*\|(?:\s*\|)?\s*", line) for line in issue.evidence.splitlines())
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence contains no empty table row to support the claim."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and agents.ingestion.page_auditor.patterns.is_valid_table_row_context(markdown, issue.evidence)
            and any(term in description for term in ("missing link formatting", "inconsistent presentation"))
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: link-style consistency in a valid table is editorial, not an ingestion defect."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and "duplicate installation command" in description
            and agents.ingestion.page_auditor.patterns.is_package_manager_alternative(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: equivalent pip and uv commands are alternative package-manager examples."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and agents.ingestion.page_auditor.patterns.is_model_configuration_code_variant(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: equivalent fenced examples differ only by model configuration."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and agents.ingestion.page_auditor.patterns.is_valid_escaped_identifier(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: escaped underscores are valid in a Markdown identifier."
            )
            continue
        if (
            issue.category == "encoding_artifact"
            and agents.ingestion.page_auditor.patterns.is_valid_numeric_entity_text(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: HTML numeric character references render as their intended characters."
            )
            continue
        if issue.category in params.CONVENTION_CATEGORIES and (
            agents.ingestion.page_auditor.patterns.is_export_convention(issue.evidence, markdown=markdown)
            or agents.ingestion.page_auditor.patterns.is_inside_export_component(markdown, issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence is valid export syntax or raw HTML, not a page defect."
            )
            continue
        if issue.category in params.DUPLICATE_CATEGORIES and not is_repeat(markdown, issue.evidence, issue.other_evidence):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence occurs once on the page and no second copy was found, so nothing is repeated."
            )
            continue
        if (
            issue.category in params.DUPLICATE_CATEGORIES
            and agents.ingestion.page_auditor.patterns.is_structural_export_markup_only(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: repeated export-block markers are structural syntax, not duplicate page content."
            )
            continue
        # Two findings on the same text can only produce overlapping patches, so keep the more confident one.
        kept = accepted.get((issue.category, issue.evidence))
        if kept is not None:
            warnings.append(
                f"Merged finding {issue.issue_id} into {kept.issue_id}: same category and evidence."
            )
            if issue.confidence <= kept.confidence:
                continue
        accepted[(issue.category, issue.evidence)] = issue

    return assessment.model_copy(update = {"issues": list(accepted.values())}), warnings


# ---- the page scans: places that may be defects, so the auditor judges leads instead of searching for them -------------------------------------
# A scan decides nothing. Its evidence is a slice of the page by construction, so a lead can never be invented; the auditor says whether it
# is a defect or how the source meant it. Code inside fences is content and is never scanned.

def prose_lines(markdown: str) -> list[tuple[int, str]]:
    """(offset, line without its newline) for every line outside a fenced code block, fence lines included in neither."""
    lines: list[tuple[int, str]] = []
    offset = 0
    opening: tuple[str, int] | None = None
    for raw in markdown.splitlines(keepends = True):
        line = raw.rstrip("\r\n")
        stripped = line.lstrip(" \t")
        fence = patterns.FENCE_RE.match(stripped)
        if opening is None and fence:
            opening = (fence.group(1)[0], len(fence.group(1)))
        elif opening is not None:
            marker, length = opening
            if re.fullmatch(rf"{re.escape(marker)}{{{length},}}\s*", stripped):
                opening = None
        else:
            lines.append((offset, line))
        offset += len(raw)
    return lines


def prose_blocks(markdown: str) -> list[tuple[int, int]]:
    """(start, end) of every paragraph-like block outside fenced code: consecutive prose lines up to a blank line or a fence."""
    blocks: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    previous_end = -1
    for offset, line in prose_lines(markdown):
        contiguous = current is not None and offset == previous_end + 1
        if not line.strip() or (current is not None and not contiguous):
            if current is not None:
                blocks.append(current)
                current = None
        if line.strip():
            current = (current[0] if current is not None else offset, offset + len(line))
        previous_end = offset + len(line)
    if current is not None:
        blocks.append(current)
    return blocks


def repeated_blocks(markdown: str) -> list[dict[str, Any]]:
    """A block of prose that appears more than once: its first copy as `evidence`, the second as `other_evidence`."""
    first: dict[str, tuple[int, int]] = {}
    seen_again: dict[str, tuple[int, int]] = {}
    counts: dict[str, int] = {}
    for start, end in prose_blocks(markdown):
        text = markdown[start:end]
        key = " ".join(text.split())
        if len(key) < params.MIN_REPEAT_CHARS or (key.startswith("#") and "\n" not in text):
            continue
        counts[key] = counts.get(key, 0) + 1
        if key not in first:
            first[key] = (start, end)
        elif key not in seen_again:
            seen_again[key] = (start, end)
    return [
        {
            "kind": "repeated_block",
            "evidence": markdown[first[key][0]:first[key][1]],
            "other_evidence": markdown[seen_again[key][0]:seen_again[key][1]],
            "note": f"the same block appears {count} times",
        }
        for key, count in counts.items()
        if count > 1
    ]


def heading_markers(markdown: str) -> list[dict[str, Any]]:
    """A heading marker with no space after it (`##Install`) and a heading with no text."""
    leads = []
    for _offset, line in prose_lines(markdown):
        if patterns.HEADING_WITHOUT_SPACE_RE.match(line):
            leads.append({"kind": "heading_without_space", "evidence": line, "note": "a `#` marker directly followed by text"})
        elif patterns.EMPTY_HEADING_RE.fullmatch(line):
            leads.append({"kind": "empty_heading", "evidence": line, "note": "a heading marker with no text"})
    return leads


def damaged_characters(markdown: str) -> list[dict[str, Any]]:
    """Text that looks like UTF-8 read as another encoding, or has the replacement character: the matching stretch of its line."""
    leads = []
    for offset, line in prose_lines(markdown):
        match = patterns.MOJIBAKE_RE.search(line)
        if match is None:
            continue
        start = max(0, match.start() - params.EVIDENCE_WINDOW // 2)
        leads.append({
            "kind": "damaged_characters",
            "evidence": line[start:start + params.EVIDENCE_WINDOW],
            "note": f"{match.group(0)!r} looks like text decoded with the wrong encoding",
        })
    return leads


def candidates(markdown: str) -> list[dict[str, Any]]:
    """Every lead on the page, first in reading order, at most `params.MAX_CANDIDATES`."""
    leads = [*heading_markers(markdown), *damaged_characters(markdown), *repeated_blocks(markdown)]
    return [
        {"candidate_id": f"c{number}", **lead}
        for number, lead in enumerate(
            sorted(leads, key = lambda lead: markdown.find(lead["evidence"]))[:params.MAX_CANDIDATES],
            start = 1,
        )
    ]
