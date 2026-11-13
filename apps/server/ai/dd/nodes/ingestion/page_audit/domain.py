"""Plain scans of an extracted page, validation and prompt-payload construction for documentation page audits."""
from __future__ import annotations
from . import params, patterns
from .... import agents

from collections import Counter
import html
import html.parser
import json
import re
import shlex
from typing import Any


class _HTMLTagCounter(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs = False)
        self.open_tags: Counter[str] = Counter()
        self.close_tags: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in patterns.VOID_HTML_TAGS:
            self.open_tags[tag] += 1

    def handle_endtag(self, tag: str) -> None:
        if tag not in patterns.VOID_HTML_TAGS:
            self.close_tags[tag] += 1


def table_cells(row: str) -> int:
    """Count table cells outside escaped pipes and code spans, excluding optional edge pipes."""
    cells = 1
    in_code = False
    escaped = False
    for char in row.strip():
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            in_code = not in_code
        elif char == "|" and not in_code:
            cells += 1
    stripped = row.strip()
    return cells - (stripped.startswith("|") + (stripped.endswith("|") and not stripped.endswith("\\|")))


def is_sound_table_head(evidence: str) -> bool:
    """Whether the evidence contains a valid GFM header and delimiter row."""
    lines = [line.strip() for line in evidence.strip().splitlines() if line.strip()]
    if not lines or not lines[0].startswith("|"):
        return False
    at = next((
        index
        for index in (0, 1)
        if index < len(lines)
        and patterns.TABLE_DELIMITER_RE.fullmatch(lines[index])
        and "|" in lines[index]
    ), None)
    if at is None:
        return False
    width = table_cells(lines[at])
    header_is_well_formed = at == 0 or table_cells(lines[0]) == width
    return header_is_well_formed and all(
        table_cells(line) <= width
        for index, line in enumerate(lines)
        if index != at
    )


def is_valid_table_row_context(markdown: str, evidence: str) -> bool:
    """Whether a cited row is part of a valid GFM table, including short body rows."""
    cited = evidence.strip()
    if not cited.startswith("|") or "\n" in cited:
        return False
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != cited:
            continue
        for delimiter_index in (index - 1, index + 1):
            if not (
                0 <= delimiter_index < len(lines)
                and "|" in lines[delimiter_index]
                and patterns.TABLE_DELIMITER_RE.fullmatch(lines[delimiter_index].strip())
            ):
                continue
            width = table_cells(lines[delimiter_index])
            header_index = delimiter_index - 1
            if (
                header_index >= 0
                and "|" in lines[header_index]
                and table_cells(lines[header_index]) == width
                and table_cells(line) <= width
            ):
                return True
    return False


def contains_valid_escaped_ampersand(evidence: str) -> bool:
    """Whether evidence includes Markdown's valid backslash escape for an ampersand."""
    return r"\&" in evidence


def contains_escaped_identifier(evidence: str) -> bool:
    """Whether evidence includes an identifier whose escaped underscores render literally."""
    return patterns.ESCAPED_IDENTIFIER_RE.search(evidence) is not None


def evidence_is_valid_atx_heading(markdown: str, evidence: str) -> bool:
    """Whether evidence occurs on a syntactically valid ATX heading line."""
    for line in markdown.splitlines():
        if evidence not in line:
            continue
        match = patterns.ATX_HEADING_LINE_RE.match(line)
        if match is None:
            continue
        content = (match.group(2) or "").strip()
        if content and not patterns.ATX_CLOSING_HASHES_RE.fullmatch(content):
            return True
    return False


def is_valid_blockquote(evidence: str) -> bool:
    """Whether the cited lines use valid blockquote markers, regardless of editorial intent."""
    lines = [line for line in evidence.splitlines() if line.strip()]
    return bool(lines) and all(patterns.BLOCKQUOTE_LINE_RE.match(line) for line in lines)


def is_package_manager_alternative(evidence: str) -> bool:
    """Whether fenced examples repeat identical requirements for pip and uv installation options."""
    blocks = patterns.FENCED_CODE_BLOCK_RE.findall(evidence)
    installs: list[tuple[str, tuple[str, ...]]] = []
    for block in blocks:
        commands = [line.strip() for line in block.splitlines() if line.strip()]
        if len(commands) != 1:
            continue
        try:
            parts = shlex.split(commands[0])
        except ValueError:
            continue
        if len(parts) < 3 or parts[:2] not in (["pip", "install"], ["pip3", "install"], ["uv", "add"]):
            continue
        installs.append(("pip" if parts[0] in {"pip", "pip3"} else "uv", tuple(parts[2:])))

    managers = {manager for manager, _requirements in installs}
    requirement_sets = {requirements for _manager, requirements in installs}
    return len(installs) >= 2 and managers == {"pip", "uv"} and len(requirement_sets) == 1


def is_model_configuration_code_variant(evidence: str) -> bool:
    """Whether fenced examples are otherwise identical and differ only by model configuration."""
    blocks = patterns.FENCED_CODE_BLOCK_RE.findall(evidence)
    values: list[str] = []
    normalized: list[str] = []
    for block in blocks:
        matches = list(patterns.MODEL_CONFIGURATION_LINE_RE.finditer(block))
        if len(matches) != 1:
            return False
        values.append(matches[0].group(2) or matches[0].group(3) or "")
        normalized.append(
            patterns.MODEL_CONFIGURATION_LINE_RE.sub(r'\1"<MODEL>"', block)
        )
    return len(blocks) >= 2 and len(set(values)) > 1 and len(set(normalized)) == 1


def is_valid_escaped_identifier(evidence: str) -> bool:
    """Whether the cited identifier uses valid Markdown escapes for its underscores."""
    return patterns.ESCAPED_IDENTIFIER_RE.fullmatch(evidence.strip()) is not None


def is_valid_numeric_entity_text(evidence: str) -> bool:
    """Whether an alleged encoding defect is valid HTML numeric character-reference text."""
    if not patterns.HTML_NUMERIC_ENTITY_RE.search(evidence):
        return False
    decoded = html.unescape(evidence)
    return decoded != evidence and "\ufffd" not in decoded


def is_structural_export_markup_only(evidence: str) -> bool:
    """Whether repeated evidence contains only HTML or GitBook block markers."""
    lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    return bool(lines) and all(
        patterns.GITBOOK_COMPONENT_TAG_RE.fullmatch(line) is not None
        or patterns.HTML_TAG_ONLY_RE.fullmatch(line) is not None
        for line in lines
    )


def is_export_convention(evidence: str, *, markdown: str = "") -> bool:
    """Whether evidence is valid site-export syntax or raw HTML, not a page defect."""
    if (
        patterns.EXPORT_CONVENTION_RE.match(evidence)
        or is_sound_table_head(evidence)
        or (markdown and is_valid_table_row_context(markdown, evidence))
    ):
        return True
    if patterns.GITBOOK_COMPONENT_TAG_RE.search(evidence) or patterns.HTML_TAG_ONLY_RE.fullmatch(evidence):
        return True
    if not markdown:
        return False
    evidence_tags = {match.group(1).lower() for match in patterns.HTML_TAG_RE.finditer(evidence)}
    if not evidence_tags:
        return False
    counts = _HTMLTagCounter()
    try:
        counts.feed(markdown)
        counts.close()
    except Exception:
        return False
    return all(
        tag in patterns.VOID_HTML_TAGS
        or (counts.open_tags[tag] > 0 and counts.open_tags[tag] == counts.close_tags[tag])
        for tag in evidence_tags
    )


def is_inside_export_component(markdown: str, evidence: str) -> bool:
    """Whether a heading or syntax finding sits inside a balanced GitBook component block."""
    start = markdown.find(evidence)
    if start < 0:
        return False
    stack: list[tuple[str, int]] = []
    spans: list[tuple[int, int]] = []
    for match in patterns.GITBOOK_COMPONENT_TAG_RE.finditer(markdown):
        name = match.group("name").lower()
        if name not in patterns.GITBOOK_COMPONENT_NAMES:
            continue
        if match.group("end"):
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == name:
                    _opened_name, opened_at = stack.pop(index)
                    spans.append((opened_at, match.start()))
                    del stack[index:]
                    break
        else:
            stack.append((name, match.end()))
    return any(opened_at <= start < closed_at for opened_at, closed_at in spans)


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
            and contains_valid_escaped_ampersand(issue.evidence)
            and "ampersand" in description
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: backslash-escaped ampersands are valid Markdown punctuation escapes."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and evidence_is_valid_atx_heading(markdown, issue.evidence)
            and (
                "colon" in description
                or ("underscore" in description and contains_escaped_identifier(issue.evidence))
            )
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the cited punctuation is part of a valid ATX heading."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and is_valid_blockquote(issue.evidence)
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
            and not any(patterns.EMPTY_TABLE_ROW_RE.fullmatch(line) for line in issue.evidence.splitlines())
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence contains no empty table row to support the claim."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and is_valid_table_row_context(markdown, issue.evidence)
            and any(term in description for term in ("missing link formatting", "inconsistent presentation"))
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: link-style consistency in a valid table is editorial, not an ingestion defect."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and "duplicate installation command" in description
            and is_package_manager_alternative(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: equivalent pip and uv commands are alternative package-manager examples."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and is_model_configuration_code_variant(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: equivalent fenced examples differ only by model configuration."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and is_valid_escaped_identifier(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: escaped underscores are valid in a Markdown identifier."
            )
            continue
        if (
            issue.category == "encoding_artifact"
            and is_valid_numeric_entity_text(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: HTML numeric character references render as their intended characters."
            )
            continue
        if issue.category in params.CONVENTION_CATEGORIES and (
            is_export_convention(issue.evidence, markdown=markdown)
            or is_inside_export_component(markdown, issue.evidence)
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
            and is_structural_export_markup_only(issue.evidence)
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
