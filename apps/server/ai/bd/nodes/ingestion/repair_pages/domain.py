"""Pure validation and safe application rules for specialist patch proposals."""
from __future__ import annotations
import domains
from . import patterns
from .... import agents

import difflib
import unicodedata
from json import JSONDecodeError, JSONDecoder
from collections.abc import Iterator
from typing import Any
from pydantic import ValidationError


class ModelCallLimitReachedError(ValueError):
    """A specialist stopped at its configured model-call limit."""


def extract_patch_response(
    result: Any,
    *,
    role: str,
) -> agents.schemas.PatchDraftBatch | agents.schemas.PatchProposalBatch:
    """Validate structured specialist output or recover a JSON response from its last message."""
    structured = result.get("structured_response") if isinstance(result, dict) else None
    if structured is not None:
        return _validate_patch_response(structured)

    messages = result.get("messages") if isinstance(result, dict) else None
    content = _last_assistant_content(messages)
    if content is None:
        raise ValueError(f"{role} specialist returned no structured response or assistant message")
    if "model call limit" in content.casefold():
        raise ModelCallLimitReachedError(f"{role} specialist reached its model-call limit")

    last_error: ValidationError | None = None
    for value in _json_objects(content):
        try:
            return _validate_patch_response(value)
        except ValidationError as error:
            last_error = error
    if last_error is not None:
        raise ValueError(f"{role} specialist returned JSON that did not match its response schema") from last_error
    raise ValueError(f"{role} specialist returned no structured response and no JSON object")


def _validate_patch_response(
    value: Any,
) -> agents.schemas.PatchDraftBatch | agents.schemas.PatchProposalBatch:
    try:
        return agents.schemas.PatchDraftBatch.model_validate(value)
    except ValidationError:
        return agents.schemas.PatchProposalBatch.model_validate(value)


def _last_assistant_content(messages: Any) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict):
            message_type = message.get("role") or message.get("type")
            content = message.get("content")
        else:
            message_type = getattr(message, "type", None)
            content = getattr(message, "content", None)
        if message_type not in {"assistant", "ai", "AIMessage"}:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if parts:
                return "\n".join(parts)
    return None


def _json_objects(content: str) -> Iterator[dict[str, Any]]:
    decoder = JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def validate_proposals(
    value: Any,
    *,
    page_number: int,
    source_sha256: str,
    markdown: str,
    assigned_issues: dict[str, agents.schemas.PageIssue],
    role: agents.schemas.SpecialistRole,
    allowed_context: str | None = None,
) -> list[agents.schemas.PatchProposal]:
    if isinstance(value, agents.schemas.PatchProposalBatch):
        proposals_to_validate = value.proposals
    else:
        try:
            drafts = (
                value
                if isinstance(value, agents.schemas.PatchDraftBatch)
                else agents.schemas.PatchDraftBatch.model_validate(value)
            )
        except ValidationError:
            # Keep stored runs and local callers using the previous full proposal shape readable.
            proposals_to_validate = agents.schemas.PatchProposalBatch.model_validate(value).proposals
        else:
            proposals_to_validate = []
            for draft in drafts.proposals:
                issue = assigned_issues.get(draft.issue_id)
                if issue is None:
                    raise ValueError("patch draft references an issue not assigned to this specialist")
                proposals_to_validate.append(agents.schemas.PatchProposal(
                    page_number = page_number,
                    issue_id = draft.issue_id,
                    source_sha256 = source_sha256,
                    issue_code = issue.code,
                    risk = draft.risk,
                    context_before = draft.context_before,
                    target_text = draft.target_text,
                    context_after = draft.context_after,
                    replacement = draft.replacement,
                    rationale = draft.rationale,
                    confidence = draft.confidence,
                    evidence = issue.evidence,
                ))

    proposals: list[agents.schemas.PatchProposal] = []
    rejected: list[str] = []
    seen_issue_ids: set[str] = set()
    for proposal in proposals_to_validate:
        if proposal.page_number != page_number:
            raise ValueError("patch proposal page does not match its source page")
        if proposal.source_sha256 != source_sha256:
            raise ValueError("patch proposal was generated for a different source version")
        if proposal.issue_id not in assigned_issues:
            raise ValueError("patch proposal references an issue not assigned to this specialist")
        if proposal.issue_id in seen_issue_ids:
            # Keep the first source-validated proposal; a duplicate must not discard the whole batch.
            continue
        if proposal.issue_code != assigned_issues[proposal.issue_id].code:
            raise ValueError("patch proposal issue code does not match its assigned issue")
        allowed = (
            proposal.issue_code in {
                agents.schemas.IssueCode.CONTROL_CHARACTERS,
                agents.schemas.IssueCode.SPLIT_WORD,
                agents.schemas.IssueCode.SOFT_HYPHEN,
                agents.schemas.IssueCode.FUSED_MARKER,
                agents.schemas.IssueCode.TEXT_CORRUPTION,
            }
            if role == agents.schemas.SpecialistRole.TEXT_REPAIR
            else proposal.issue_code in {
                agents.schemas.IssueCode.STRUCTURE,
                agents.schemas.IssueCode.TOC_NOISE,
                agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT,
            }
        )
        if not allowed:
            raise ValueError("patch proposal issue code is outside this specialist's scope")
        if _invents_text(proposal):
            continue
        # A target the page cannot support loses only its own proposal; its issue stays unresolved for review.
        if not proposal.target_text or proposal.target_text not in markdown:
            rejected.append("patch proposal target is not present in the source page")
            continue
        if allowed_context is not None and proposal.target_text not in allowed_context:
            rejected.append("patch proposal target is outside the supplied repair context")
            continue
        start = markdown.find(proposal.target_text)
        if markdown.find(proposal.target_text, start + 1) >= 0 and not all(
            _is_spacing_repair(proposal, markdown, found, found + len(proposal.target_text))
            for found in _occurrences(markdown, proposal.target_text)
        ):
            # Only a verified space-only repair may name a phrase that occurs more than once: it is the same fix at each.
            rejected.append("patch proposal target is ambiguous in the source page")
            continue
        end = start + len(proposal.target_text)
        # The target is exact and unique, so the patch is fully determined; quoted anchors only disambiguate and
        # models often paraphrase them. A wrong anchor is dropped instead of discarding an otherwise valid patch.
        if proposal.context_before and not markdown[:start].endswith(proposal.context_before):
            proposal = proposal.model_copy(update = {"context_before": ""})
        if proposal.context_after and not markdown[end:].startswith(proposal.context_after):
            proposal = proposal.model_copy(update = {"context_after": ""})
        if allowed_context is not None:
            anchored_text = f"{proposal.context_before}{proposal.target_text}{proposal.context_after}"
            if anchored_text not in allowed_context:
                raise ValueError("patch proposal anchor is outside the supplied repair context")
        for evidence in proposal.evidence:
            if evidence.source != agents.schemas.EvidenceSource.PAGE_MARKDOWN:
                raise ValueError("patch evidence must come from the supplied Markdown page")
            if evidence.page_number != page_number or evidence.excerpt not in markdown:
                raise ValueError("patch evidence is not an exact excerpt from the supplied page")
        seen_issue_ids.add(proposal.issue_id)
        proposals.append(proposal)

    if rejected and not proposals:
        # Nothing usable survived, so let the caller retry with the reason instead of reporting a silent no-op.
        raise ValueError(rejected[0])
    return proposals


def _invents_text(proposal: agents.schemas.PatchProposal) -> bool:
    """True when a replacement adds words, links, images or markers that the page never contained."""
    target, replacement = proposal.target_text, proposal.replacement
    # A page may end mid-sentence because its paragraph continues on the next page; added words are invented text.
    if replacement.startswith(target) and len(replacement[len(target):].split()) > 1:
        return True
    squeezed_target = patterns._SPACE_RE.sub("", target)
    return any(
        patterns._SPACE_RE.sub("", introduced) not in squeezed_target
        for introduced in (
            *patterns._LINK_DESTINATION_RE.findall(replacement),
            *patterns._HTML_COMMENT_RE.findall(replacement),
        )
    )


def build_repair_context(
    markdown: str,
    issues: list[agents.schemas.PageIssue],
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


def apply_proposals(
    markdown: str,
    proposals: list[agents.schemas.PatchProposal],
) -> tuple[str, list[str], list[str]]:
    # One span per occurrence: a space-only repair of a phrase that appears twice is the same fix in both places.
    spans = [
        (start, start + len(proposal.target_text), proposal)
        for proposal in proposals
        for start in _occurrences(markdown, proposal.target_text)
    ]
    spans.sort(key = lambda span: span[0])
    # A proposal qualifies only if it does at every occurrence. A space-only edit is verified by the diff itself, so the
    # model's risk label and confidence only need to rule out a guess; a letter-changing edit never qualifies.
    eligible_issue_ids = {
        proposal.issue_id
        for proposal in proposals
        if all(
            _is_auto_applicable(proposal, markdown, start, start + len(proposal.target_text))
            for start in _occurrences(markdown, proposal.target_text)
        )
    }
    eligible = [span for span in spans if span[2].issue_id in eligible_issue_ids]
    # Only two edits that could both be applied can collide; a review-only proposal never touches the page, so it must
    # not hold back a verified repair that happens to sit inside its span.
    overlapping_issue_ids: set[str] = set()
    for index, current in enumerate(eligible):
        for previous in eligible[:index]:
            if current[0] < previous[1] and previous[0] < current[1]:
                overlapping_issue_ids.add(current[2].issue_id)
                overlapping_issue_ids.add(previous[2].issue_id)

    normalized = markdown
    applied_issue_ids: dict[str, None] = {}
    review_issue_ids: dict[str, None] = {}
    for start, end, proposal in reversed(spans):
        auto_apply = proposal.issue_id in eligible_issue_ids and proposal.issue_id not in overlapping_issue_ids
        if auto_apply:
            normalized = normalized[:start] + proposal.replacement + normalized[end:]
            applied_issue_ids[proposal.issue_id] = None
        else:
            review_issue_ids[proposal.issue_id] = None
    return normalized, list(reversed(applied_issue_ids)), list(reversed(review_issue_ids))


def _occurrences(markdown: str, target: str) -> list[int]:
    starts = []
    start = markdown.find(target)
    while start >= 0:
        starts.append(start)
        start = markdown.find(target, start + len(target))
    return starts


def _is_auto_applicable(proposal: agents.schemas.PatchProposal, markdown: str, start: int, end: int) -> bool:
    return (
        proposal.risk == agents.schemas.PatchRisk.LOW
        and proposal.confidence >= domains.bd.ingestion.ai.params.AUTO_APPLY_CONFIDENCE
        and _is_mechanical_repair(proposal)
        or proposal.risk in {agents.schemas.PatchRisk.LOW, agents.schemas.PatchRisk.MEDIUM}
        and proposal.confidence >= domains.bd.ingestion.ai.params.SPACING_REPAIR_CONFIDENCE
        and _is_spacing_repair(proposal, markdown, start, end)
    )


def apply_mechanical_fixes(markdown: str) -> tuple[str, dict[str, int]]:
    """Normalize invisible noise and safe Markdown table formatting."""
    cleaned, counts, _ = _apply_mechanical_fixes(markdown)
    return cleaned, counts


def apply_mechanical_fixes_for_issues(
    markdown: str,
    issues: list[agents.schemas.PageIssue],
) -> tuple[str, dict[str, int], list[str]]:
    """Normalize deterministic defects and return only findings whose source evidence was actually repaired."""
    cleaned, counts, table_changes = _apply_mechanical_fixes(markdown)
    resolved_issue_ids = []
    for issue in issues:
        if issue.code in {
            agents.schemas.IssueCode.CONTROL_CHARACTERS,
            agents.schemas.IssueCode.SOFT_HYPHEN,
        }:
            # The cleaned text holds none of either, whether this pass removed them or an earlier repair already had
            # (a space-only repair can swallow the soft hyphen it sits beside).
            resolved_issue_ids.append(issue.issue_id)
            continue
        if issue.code == agents.schemas.IssueCode.TOC_NOISE:
            covered = all(
                _evidence_was_changed_with_toc_cleanup(evidence.excerpt, table_changes)
                for evidence in issue.evidence
            )
            if covered:
                resolved_issue_ids.append(issue.issue_id)
        elif issue.code == agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT:
            covered = all(
                _evidence_was_changed_in_table(evidence.excerpt, table_changes)
                for evidence in issue.evidence
            )
            if covered:
                resolved_issue_ids.append(issue.issue_id)
    return cleaned, counts, resolved_issue_ids


def _apply_mechanical_fixes(markdown: str) -> tuple[str, dict[str, int], list[tuple[str, str]]]:
    """Return cleaned text, operation counts, and changed table blocks for evidence matching."""
    control_characters = sum(_is_control_noise(character) for character in markdown)
    soft_hyphens = markdown.count("­")
    cleaned = "".join(
        character for character in markdown
        if character != "­" and not _is_control_noise(character)
    )
    cleaned, table_count, table_changes = _normalize_markdown_tables(cleaned)
    counts = {
        agents.schemas.IssueCode.CONTROL_CHARACTERS.value: control_characters,
        agents.schemas.IssueCode.SOFT_HYPHEN.value: soft_hyphens,
        "table_cleanup": table_count,
    }
    return cleaned, {code: count for code, count in counts.items() if count}, table_changes


def _normalize_markdown_tables(markdown: str) -> tuple[str, int, list[tuple[str, str]]]:
    lines = markdown.splitlines(keepends = True)
    blocks: list[tuple[int, int]] = []
    in_fence = False
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence or index == 0:
            index += 1
            continue
        separator = _split_table_cells(lines[index])
        header = _split_table_cells(lines[index - 1])
        if len(separator) < 2 or len(header) != len(separator) or not all(
            patterns._TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in separator
        ):
            index += 1
            continue
        start = index - 1
        end = index + 1
        while end < len(lines) and lines[end].strip() and "|" in lines[end]:
            end += 1
        blocks.append((start, end))
        index = end

    changes: list[tuple[str, str]] = []
    for start, end in reversed(blocks):
        original_lines = lines[start:end]
        transformed_lines = _normalize_table_block(original_lines)
        original = "".join(original_lines)
        transformed = "".join(transformed_lines)
        if original == transformed:
            continue
        changes.append((original, transformed))
        lines[start:end] = transformed_lines

    changes.reverse()
    return "".join(lines), len(changes), changes


def _normalize_table_block(lines: list[str]) -> list[str]:
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    final_newline = lines[-1].endswith(("\n", "\r"))
    rows = [_split_table_cells(line) for line in lines]
    expected_columns = len(rows[1])
    if any(len(row) != expected_columns for row in rows):
        return lines

    header, separator, *body = rows
    body = [row for row in body if any(cell.strip() for cell in row)]
    rows = [header, separator, *body]
    rows = _remove_table_toc_leaders(rows)
    header, separator, *body = rows
    body = [row for row in body if any(cell.strip() for cell in row)]
    rows = [header, separator, *body]

    removable = {
        column
        for column in range(expected_columns)
        if all(not row[column].strip() for row in [header, *body])
    }
    if len(removable) == expected_columns:
        return []
    if removable:
        rows = [[cell for column, cell in enumerate(row) if column not in removable] for row in rows]

    duplicate_columns = set()
    for column in range(1, len(rows[0])):
        if (
            len(rows[0]) - len(duplicate_columns) > 1
            and all(row[column].strip() == row[column - 1].strip() for row in [rows[0], *rows[2:]])
        ):
            if any(row[column].strip() for row in [rows[0], *rows[2:]]):
                duplicate_columns.add(column)
    if duplicate_columns:
        rows = [[cell for column, cell in enumerate(row) if column not in duplicate_columns] for row in rows]

    formatted = ["| " + " | ".join(cell.strip() for cell in row) + " |" for row in rows]
    if final_newline:
        formatted[-1] += newline
    return [line + newline for line in formatted[:-1]] + formatted[-1:]


def _remove_table_toc_leaders(rows: list[list[str]]) -> list[list[str]]:
    cleaned = [row.copy() for row in rows]
    if not cleaned:
        return cleaned
    for row in cleaned[2:]:
        nonempty = [index for index, cell in enumerate(row) if cell.strip()]
        if not nonempty:
            continue
        last = nonempty[-1]
        last_cell = row[last].strip()
        leader_page = patterns._LEADING_DOT_LEADER_PAGE_REFERENCE_RE.fullmatch(last_cell)
        if leader_page:
            row[last] = leader_page.group("page")
            last_cell = row[last]
        if patterns._PAGE_REFERENCE_RE.fullmatch(last_cell):
            for column in nonempty[:-1]:
                title = patterns._TRAILING_LEADERS_RE.sub("", row[column]).rstrip()
                if title != row[column].strip():
                    row[column] = title
        for column, cell in enumerate(row):
            match = patterns._INLINE_TOC_ROW_RE.fullmatch(cell.strip())
            if match:
                row[column] = f"{match.group('title').rstrip()} {match.group('page')}"
    return cleaned


def _split_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    code_ticks = 0
    index = 0
    while index < len(stripped):
        character = stripped[index]
        if escaped:
            current.append(character)
            escaped = False
            index += 1
            continue
        if character == "\\":
            current.append(character)
            escaped = True
            index += 1
            continue
        if character == "`":
            run_end = index + 1
            while run_end < len(stripped) and stripped[run_end] == "`":
                run_end += 1
            run = run_end - index
            if code_ticks == 0:
                code_ticks = run
            elif code_ticks == run:
                code_ticks = 0
            current.extend(stripped[index:run_end])
            index = run_end
            continue
        if character == "|" and code_ticks == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    cells.append("".join(current).strip())
    if stripped.startswith("|") and cells:
        cells.pop(0)
    if stripped.endswith("|") and cells:
        cells.pop()
    return cells


def _evidence_was_changed_in_table(excerpt: str, changes: list[tuple[str, str]]) -> bool:
    return any(excerpt in original and excerpt not in transformed for original, transformed in changes)


def _evidence_was_changed_with_toc_cleanup(excerpt: str, changes: list[tuple[str, str]]) -> bool:
    cells = _split_table_cells(excerpt) if "|" in excerpt else [excerpt]
    has_toc_markers = any(
        patterns._TRAILING_LEADERS_RE.search(cell)
        or patterns._INLINE_TOC_ROW_RE.fullmatch(cell.strip())
        or patterns._LEADING_DOT_LEADER_PAGE_REFERENCE_RE.fullmatch(cell.strip())
        for cell in cells
    )
    return bool(has_toc_markers) and _evidence_was_changed_in_table(excerpt, changes)


def _is_control_noise(character: str) -> bool:
    return unicodedata.category(character) == "Cc" and character not in "\t\n\r"


def _is_spacing_repair(proposal: agents.schemas.PatchProposal, markdown: str, start: int, end: int) -> bool:
    """True when only spaces differ (plus intra-word hyphens in plain prose), so no word, URL or number was rewritten."""
    if proposal.issue_code not in {
        agents.schemas.IssueCode.SPLIT_WORD,
        agents.schemas.IssueCode.FUSED_MARKER,
        agents.schemas.IssueCode.TEXT_CORRUPTION,
    }:
        return False
    target, replacement = proposal.target_text, proposal.replacement
    if not any(
        evidence.excerpt in target or target in evidence.excerpt
        for evidence in proposal.evidence
    ):
        return False
    if _inserts_space(target, replacement) and _inside_path_or_address(markdown, start, end):
        return False
    if (
        target[:len(target) - len(target.lstrip())] != replacement[:len(replacement) - len(replacement.lstrip())]
        or target[len(target.rstrip()):] != replacement[len(replacement.rstrip()):]
        or patterns._MARKDOWN_MARKER_RE.findall(target) != patterns._MARKDOWN_MARKER_RE.findall(replacement)
    ):
        return False
    squeezed_target, squeezed_replacement = (
        patterns._SPACE_RE.sub("", text) for text in (target, replacement)
    )
    if squeezed_target == squeezed_replacement:
        return True
    if "\\_" in squeezed_target and squeezed_target.replace("\\_", "_") == squeezed_replacement:
        # Markdown renders "\_" as "_", so dropping the backslash (and the stray space beside it) in a reference URL changes
        # nothing a reader sees; inside code the backslash is a real character and stays.
        return not _inside_code(markdown, start)
    if not all(patterns._PLAIN_PROSE_RE.fullmatch(text) for text in (target, replacement)):
        return False
    # A hyphen may be added, or dropped where the source left it dangling before a space ("engineer- ing");
    # a hyphen that is already part of a word is never removed.
    joined_target = patterns._DANGLING_HYPHEN_RE.sub("", target)
    return (
        patterns._INTRA_WORD_HYPHEN_RE.sub("", patterns._SPACE_RE.sub("", joined_target))
        == patterns._INTRA_WORD_HYPHEN_RE.sub("", squeezed_replacement)
        and replacement.count("-") >= joined_target.count("-")
    )


def _inserts_space(target: str, replacement: str) -> bool:
    matcher = difflib.SequenceMatcher(None, target, replacement, autojunk = False)
    return any(
        tag in {"insert", "replace"} and patterns._SPACE_RE.search(replacement[j1:j2])
        for tag, _, _, j1, j2 in matcher.get_opcodes()
    )


def _inside_code(markdown: str, start: int) -> bool:
    """True in a fenced block, or on a line that holds an inline code span."""
    line_start = markdown.rfind("\n", 0, start) + 1
    line_end = markdown.find("\n", start)
    line = markdown[line_start:line_end if line_end >= 0 else len(markdown)]
    return markdown.count("```", 0, start) % 2 == 1 or "`" in line


def _inside_path_or_address(markdown: str, start: int, end: int) -> bool:
    """True when the unbroken text around a span holds a URL, path or address, where a space is never correct."""
    left, right = start, end
    while left > 0 and not markdown[left - 1].isspace():
        left -= 1
    while right < len(markdown) and not markdown[right].isspace():
        right += 1
    return any(character in markdown[left:right] for character in "/\\@")


def _is_mechanical_repair(proposal: agents.schemas.PatchProposal) -> bool:
    if proposal.issue_code == agents.schemas.IssueCode.SOFT_HYPHEN:
        return "\u00ad" in proposal.target_text and proposal.replacement == proposal.target_text.replace("\u00ad", "")
    if proposal.issue_code == agents.schemas.IssueCode.CONTROL_CHARACTERS:
        expected = "".join(
            character
            for character in proposal.target_text
            if unicodedata.category(character) != "Cc" or character in "\t\n\r"
        )
        return expected != proposal.target_text and proposal.replacement == expected
    return False
