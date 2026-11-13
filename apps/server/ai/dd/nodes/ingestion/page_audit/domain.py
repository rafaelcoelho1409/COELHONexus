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
    if not cited or "\n" in cited:
        return False
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if cited not in line.strip() or not line.strip().startswith("|"):
            continue
        table_start = index
        while table_start > 0 and lines[table_start - 1].strip().startswith("|"):
            table_start -= 1
        delimiter_index = table_start + 1
        if (
            delimiter_index < len(lines)
            and patterns.TABLE_DELIMITER_RE.fullmatch(lines[delimiter_index].strip())
        ):
            width = table_cells(lines[delimiter_index])
            if table_cells(lines[table_start]) == width and table_cells(line) <= width:
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


def is_all_evidence_valid_atx_headings(markdown: str, evidence: str) -> bool:
    """Whether every non-empty cited line is a valid ATX heading in the source."""
    lines = [line for line in evidence.splitlines() if line.strip()]
    return bool(lines) and all(
        patterns.ATX_HEADING_LINE_RE.match(line) is not None
        and evidence_is_valid_atx_heading(markdown, line.strip())
        for line in lines
    )


def is_sphinx_definition_list_fragment(markdown: str, evidence: str) -> bool:
    """Whether the finding cites a Sphinx API term or its colon-prefixed definition body."""
    source_lines = markdown.splitlines()
    evidence_lines = evidence.splitlines()
    start = markdown.find(evidence)
    if start < 0:
        return False
    source_index = markdown.count("\n", 0, start)
    for offset, line in enumerate(evidence_lines):
        index = source_index + offset
        if index >= len(source_lines):
            break
        if (
            patterns.SPHINX_TERM_LINE_RE.fullmatch(source_lines[index]) is not None
            and index + 1 < len(source_lines)
            and patterns.SPHINX_DEFINITION_LINE_RE.match(source_lines[index + 1]) is not None
        ):
            return True
        if (
            patterns.SPHINX_DEFINITION_LINE_RE.match(source_lines[index]) is not None
            and index > 0
            and patterns.SPHINX_TERM_LINE_RE.fullmatch(source_lines[index - 1]) is not None
        ):
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
    """Whether every fenced example belongs to a repeated template that differs only in model configuration."""
    blocks = patterns.FENCED_CODE_BLOCK_RE.findall(evidence)
    groups: dict[str, list[tuple[str, ...]]] = {}
    for block in blocks:
        matches = list(patterns.MODEL_CONFIGURATION_LINE_RE.finditer(block))
        if not matches:
            return False
        values = tuple(match.group(2) or match.group(3) or "" for match in matches)
        normalized = patterns.MODEL_CONFIGURATION_LINE_RE.sub(r'\1"<MODEL>"', block)
        groups.setdefault(normalized, []).append(values)
    return len(blocks) >= 2 and bool(groups) and all(
        len(variants) >= 2 and len(set(variants)) > 1
        for variants in groups.values()
    )


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


def repeated_prefix_copies(evidence: str) -> tuple[str, str] | None:
    """Find two adjacent, long paragraph copies whose only difference is Markdown line wrapping."""
    tokens = list(re.finditer(r"\S+", evidence))
    minimum_tokens = 25
    if len(tokens) < minimum_tokens * 2:
        return None
    for boundary in range(minimum_tokens, len(tokens) // 2 + 1):
        first_tokens = [match.group() for match in tokens[:boundary]]
        second_tokens = [match.group() for match in tokens[boundary:boundary * 2]]
        first = evidence[tokens[0].start():tokens[boundary - 1].end()]
        second = evidence[tokens[boundary].start():tokens[boundary * 2 - 1].end()]
        if (
            first_tokens == second_tokens
            and len(" ".join(first.split())) >= 160
            and first.strip() != second.strip()
        ):
            return first, second
    return None


def _heading_scope_at(markdown: str, offset: int) -> tuple[str, int] | None:
    """Return the closest preceding ATX heading and its line index for one source offset."""
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer("\n", markdown))
    lines = markdown.splitlines()
    line_index = max(
        (index for index, start in enumerate(line_starts) if start <= offset),
        default = 0,
    )
    heading_index = next((
        index
        for index in range(line_index, -1, -1)
        if patterns.ATX_HEADING_LINE_RE.match(lines[index]) is not None
    ), None)
    return (lines[heading_index].strip(), heading_index) if heading_index is not None else None


def is_usage_example_link_scoped_to_distinct_sections(markdown: str, evidence: str) -> bool:
    """Whether a repeated usage-example link is separately scoped under distinct integration headings."""
    if re.fullmatch(r"See a \[usage example\]\([^\s)]+\)\.", evidence.strip(), re.IGNORECASE) is None:
        return False
    scopes = [
        _heading_scope_at(markdown, match.start())
        for match in re.finditer(re.escape(evidence.strip()), markdown)
    ]
    return len(scopes) >= 2 and all(scope is not None for scope in scopes) and len(set(scopes)) == len(scopes)


def is_curated_table_overlap(markdown: str, evidence: str, other_evidence: str) -> bool:
    """Whether repeated table rows appear under a curated/top heading and the broader all-items table."""
    if not evidence.strip().startswith("|") or not other_evidence.strip().startswith("|"):
        return False
    first_row = next((line.strip() for line in evidence.splitlines() if line.strip()), "")
    second_row = next((line.strip() for line in other_evidence.splitlines() if line.strip()), "")
    if not first_row or not second_row or not (
        is_valid_table_row_context(markdown, first_row)
        and is_valid_table_row_context(markdown, second_row)
    ):
        return False
    first_at = markdown.find(first_row)
    second_at = markdown.find(second_row, first_at + len(first_row)) if first_at >= 0 else -1
    if first_at < 0 or second_at < 0:
        return False
    first_scope = _heading_scope_at(markdown, first_at)
    second_scope = _heading_scope_at(markdown, second_at)
    if first_scope is None or second_scope is None or first_scope == second_scope:
        return False
    headings = (first_scope[0].casefold(), second_scope[0].casefold())
    has_curated = any(re.search(r"\b(?:featured|top|recommended)\b", heading) for heading in headings)
    has_all = any(re.search(r"\b(?:all|available|complete)\b", heading) for heading in headings)
    return has_curated and has_all


def is_valid_wrapped_list_item(evidence: str) -> bool:
    """Whether lines after an ordered/unordered list marker are valid indented continuations."""
    lines = evidence.splitlines()
    if len(lines) < 2:
        return False
    first = re.match(r"^(?P<indent>[ \t]*)(?P<marker>(?:\d+[.)]|[-+*])[ \t]+)\S", lines[0])
    if first is None:
        return False
    continuation_indent = len(first.group("indent")) + len(first.group("marker"))
    return all(
        not line.strip() or (line.startswith(" ") and len(line) - len(line.lstrip(" ")) >= continuation_indent)
        for line in lines[1:]
    )


def is_normal_spacing_between_code_blocks(evidence: str) -> bool:
    """Whether multiple fenced examples are separated by the single blank line Markdown normally uses."""
    lines = evidence.splitlines()
    ranges: list[tuple[int, int]] = []
    opening: tuple[str, int, int] | None = None
    for index, line in enumerate(lines):
        match = patterns.FENCE_RE.match(line.lstrip(" \t"))
        if match is None:
            continue
        marker = match.group(1)
        if opening is None:
            opening = (marker[0], len(marker), index)
        elif marker[0] == opening[0] and len(marker) >= opening[1]:
            rest = line.lstrip(" \t")[len(marker):]
            if not rest.strip():
                ranges.append((opening[2], index))
                opening = None
    if len(ranges) < 2:
        return False
    for previous, current in zip(ranges, ranges[1:]):
        gap = lines[previous[1] + 1:current[0]]
        if gap != [""]:
            return False
    return True


def is_scoped_repeat_across_api_entries(markdown: str, evidence: str) -> bool:
    """Whether repeated evidence belongs to separate source-linked API entries rather than one duplicated entry."""
    if not evidence:
        return False

    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer("\n", markdown))
    lines = markdown.splitlines()
    scoped_headings: set[str] = set()
    occurrence_count = 0
    for occurrence in re.finditer(re.escape(evidence), markdown):
        occurrence_count += 1
        line_index = max(
            (index for index, start in enumerate(line_starts) if start <= occurrence.start()),
            default = 0,
        )
        heading_index = next((
            index
            for index in range(line_index, -1, -1)
            if patterns.ATX_HEADING_LINE_RE.match(lines[index]) is not None
        ), None)
        if heading_index is None:
            return False
        heading = lines[heading_index].strip()
        source_link_found = False
        for line in lines[heading_index + 1:min(heading_index + 5, len(lines))]:
            if not line.strip():
                continue
            if patterns.API_SOURCE_LINK_RE.fullmatch(line.strip()):
                source_link_found = True
            break
        if not source_link_found or heading in scoped_headings:
            return False
        scoped_headings.add(heading)

    return occurrence_count >= 2 and len(scoped_headings) == occurrence_count


def _sphinx_scope_at(markdown: str, offset: int) -> tuple[str, str] | None:
    """Return a Sphinx API term and enclosing heading for one source offset."""
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer("\n", markdown))
    lines = markdown.splitlines()
    line_index = max(
        (index for index, start in enumerate(line_starts) if start <= offset),
        default = 0,
    )
    term_index = next((
        index
        for index in range(line_index, -1, -1)
        if patterns.SPHINX_TERM_LINE_RE.fullmatch(lines[index]) is not None
        and index + 1 < len(lines)
        and patterns.SPHINX_DEFINITION_LINE_RE.match(lines[index + 1]) is not None
    ), None)
    if term_index is None:
        return None
    heading = next((
        lines[index].strip()
        for index in range(term_index, -1, -1)
        if patterns.ATX_HEADING_LINE_RE.match(lines[index]) is not None
    ), "")
    term = " ".join(lines[term_index].split()).casefold()
    return heading.casefold(), term


def is_scoped_repeat_across_sphinx_entries(markdown: str, evidence: str, other_evidence: str) -> bool:
    """Whether duplicate evidence belongs to separate Sphinx signature/definition-list entries."""
    offsets = sorted({
        match.start()
        for value in (evidence.strip(), other_evidence.strip())
        if value
        for match in re.finditer(re.escape(value), markdown)
    })
    scopes = [_sphinx_scope_at(markdown, offset) for offset in offsets]
    return len(scopes) >= 2 and all(scope is not None for scope in scopes) and len(set(scopes)) == len(scopes)


def is_reported_exclamation_mark_a_figure_image(markdown: str, evidence: str, description: str) -> bool:
    """Reject a claimed stray `!` when it is the image marker in a valid stored figure and the claim says it is solitary."""
    if evidence.strip() != "!" or not re.search(r"\b(?:solitary|stray)\s+exclamation\b", description, re.IGNORECASE):
        return False
    return any(
        "![](" in line and re.search(r"!\[\]\([^\s)]+\)", line)
        for line in markdown.splitlines()
    )


def is_variadic_parameter_naming_preference(description: str) -> bool:
    """Whether an alleged source defect is only a preference for `args`/`kwargs` naming conventions."""
    normalized = description.casefold()
    return (
        any(term in normalized for term in ("parameter name", "argument name", "variadic positional"))
        and "convention" in normalized
        and any(term in normalized for term in ("args", "kwargs"))
    )


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
            issue.category in {"markdown_formatting", "other"}
            and is_variadic_parameter_naming_preference(issue.description)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: a parameter naming convention is not an ingestion defect."
            )
            continue
        repeated_copies = (
            repeated_prefix_copies(issue.evidence)
            if issue.category == "markdown_formatting"
            and re.search(r"(?:duplicate|repeated).{0,40}paragraph", description)
            else None
        )
        if repeated_copies is not None:
            issue = issue.model_copy(update = {
                "issue_id": re.sub(r"-markdown_formatting(-\d+)$", r"-duplicate_section\1", issue.issue_id),
                "category": agents.schemas.IssueCategory.DUPLICATE_SECTION,
                "description": "A long paragraph prefix is repeated in the same section; the complete later copy should be kept.",
                "evidence": repeated_copies[0],
                "other_evidence": repeated_copies[1],
            })
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
            issue.category in {"markdown_formatting", "malformed_heading"}
            and evidence_is_valid_atx_heading(markdown, issue.evidence)
            and (
                "colon" in description
                or ("underscore" in description and contains_escaped_identifier(issue.evidence))
                or any(term in description for term in ("lacks a space", "no space after", "malformed heading", "heading hierarchy", "no h1"))
            )
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the cited punctuation is part of a valid ATX heading."
            )
            continue
        if (
            issue.category in {"markdown_formatting", "malformed_heading"}
            and is_all_evidence_valid_atx_headings(markdown, issue.evidence)
            and any(term in description for term in ("heading", "h1", "hierarchy", "malformed"))
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the cited heading lines are valid ATX headings in the source."
            )
            continue
        if (
            issue.category in {"markdown_formatting", "malformed_heading"}
            and is_sphinx_definition_list_fragment(markdown, issue.evidence)
            and any(term in description for term in ("format", "syntax", "heading", "colon", "space", "indent", "definition"))
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the cited API signature uses Sphinx definition-list syntax."
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
            and is_package_manager_alternative(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: equivalent pip and uv commands are alternative package-manager examples."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and is_valid_wrapped_list_item(issue.evidence)
            and any(term in description for term in ("line break", "visual flow", "wrap"))
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the numbered list item uses a valid indented continuation line."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and "consecutive blank lines" in description
            and is_normal_spacing_between_code_blocks(issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the code examples are separated by one normal Markdown blank line."
            )
            continue
        if (
            issue.category == "markdown_formatting"
            and is_reported_exclamation_mark_a_figure_image(markdown, issue.evidence, issue.description)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the cited exclamation mark is a valid image marker inside a figure."
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
            and contains_escaped_identifier(issue.evidence)
            and any(term in description for term in ("underscore", "escape", "backslash"))
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
            and is_usage_example_link_scoped_to_distinct_sections(markdown, issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the usage-example link appears under separate integration sections."
            )
            continue
        if (
            issue.category in params.DUPLICATE_CATEGORIES
            and is_curated_table_overlap(markdown, issue.evidence, issue.other_evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: the repeated table entries are shared by curated and all-items listings."
            )
            continue
        if (
            issue.category in params.DUPLICATE_CATEGORIES
            and is_scoped_repeat_across_api_entries(markdown, issue.evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: repeated text is scoped to separate source-linked API entries."
            )
            continue
        if (
            issue.category in params.DUPLICATE_CATEGORIES
            and is_scoped_repeat_across_sphinx_entries(markdown, issue.evidence, issue.other_evidence)
        ):
            warnings.append(
                f"Discarded finding {issue.issue_id}: repeated method text belongs to separate Sphinx API entries."
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
