"""Source-version and evidence validation for page repair proposals."""
from __future__ import annotations

from . import params
from .. import page_audit
from .... import agents

import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError


def repairable_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    """The audit narrowed to findings a patch could pass: an eligible category the auditor is confident enough about."""
    return {
        **assessment,
        "issues": [
            issue
            for issue in assessment["issues"]
            if issue["category"] in params.PATCHABLE_CATEGORIES
            and issue["confidence"] >= params.MIN_PATCH_CONFIDENCE
        ],
    }


def dropped_words(target: str, replacement: str) -> set[str]:
    """The words of `target` that `replacement` no longer has; a formatting-only edit must leave this empty."""
    return set(re.findall(r"\w+", target.casefold())) - set(re.findall(r"\w+", replacement.casefold()))


def quote_depth(line: str) -> int:
    """How many block-quote markers open `line` (`> > x` is 2)."""
    depth = 0
    rest = line
    marker = re.match(r"[ \t]{0,3}>[ \t]?", rest)
    while marker:
        depth += 1
        rest = rest[marker.end():]
        marker = re.match(r"[ \t]{0,3}>[ \t]?", rest)
    return depth


def cuts_block_quote(markdown: str, start: int, end: int, replacement: str) -> bool:
    """Whether a patch takes the quote markers off part of a block quote that goes on around it: the edited lines are less quoted than they
    were while the line above or below still is, which splits a callout, tab or card in two. A quote taken apart whole (the patch's lines
    are the quote's first and last) is not cut."""
    line_start = markdown.rfind("\n", 0, start) + 1
    line_end = markdown.find("\n", end)
    line_end = len(markdown) if line_end == -1 else line_end
    old = markdown[line_start:line_end]
    new = markdown[line_start:start] + replacement + markdown[end:line_end]

    def shallowest(text: str) -> int:
        depths = [quote_depth(line) for line in text.split("\n") if line.strip()]
        return min(depths) if depths else 0

    depth = shallowest(old)
    if depth == 0 or shallowest(new) >= depth:
        return False
    above = markdown[:line_start].rstrip("\n").rsplit("\n", 1)[-1] if line_start else ""
    below = markdown[line_end + 1:].split("\n", 1)[0] if line_end < len(markdown) else ""
    return quote_depth(above) >= depth or quote_depth(below) >= depth


def duplicate_survives(markdown: str, evidence: str, target: str, replacement: str) -> bool:
    """Whether the text a removal claims is repeated is still on the page once the patch is applied: a duplicate has a
    surviving copy, and an edit that deletes the only one is a deletion, not a deduplication."""
    return evidence.strip() in markdown.replace(target, replacement, 1)


def patch_rejection(
    markdown: str,
    source_sha256: str,
    known_issues: dict[str, dict[str, Any]],
    patch: agents.schemas.PagePatch,
) -> tuple[str, int, int]:
    """Why `patch` cannot be applied to `markdown` on its own ("" when it can) and the span it would replace. The same rules decide
    a patch for the final gate (`apply_patches`) and for the repairer's `check_patch` tool, so a patch the tool accepts is a patch the gate
    accepts."""
    category = known_issues[patch.issue_id]["category"] if patch.issue_id in known_issues else ""
    if patch.source_sha256 != source_sha256:
        return "source hash mismatch", 0, 0
    if patch.issue_id not in known_issues:
        return "patch does not reference an accepted finding", 0, 0
    if patch.evidence != known_issues[patch.issue_id]["evidence"]:
        return "patch evidence does not match its audited finding", 0, 0
    if not (patch.evidence in patch.target_text or patch.target_text in patch.evidence):
        return "patch target does not overlap its audited evidence", 0, 0
    if category not in params.PATCHABLE_CATEGORIES:
        return "finding category is not eligible for automatic repair", 0, 0
    if patch.confidence < params.MIN_PATCH_CONFIDENCE:
        return "confidence below the automatic-apply threshold", 0, 0
    if patch.evidence not in markdown:
        return "evidence is not an exact source substring", 0, 0
    if markdown.count(patch.target_text) != 1:
        return "target does not occur exactly once", 0, 0
    if category in params.WORD_PRESERVING_CATEGORIES and dropped_words(patch.target_text, patch.replacement):
        return "formatting patch removes words from the page", 0, 0
    if category in page_audit.params.DUPLICATE_CATEGORIES and not duplicate_survives(
        markdown, patch.evidence, patch.target_text, patch.replacement
    ):
        return "removing it would leave no copy of the text", 0, 0
    start = markdown.index(patch.target_text)
    end = start + len(patch.target_text)
    if overlaps_fenced_code(markdown, start, end):
        return "patch target overlaps a fenced code block", start, end
    if cuts_block_quote(markdown, start, end, patch.replacement):
        return "patch takes the quote markers off part of a block quote", start, end
    before =markdown[max(0, start - len(patch.context_before)):start]
    after = markdown[end:end + len(patch.context_after)]
    if before != patch.context_before or after != patch.context_after:
        return "surrounding context mismatch", start, end
    return "", start, end


def check_patch(markdown: str, source_sha256: str, assessment: dict[str, Any], fields: dict[str, Any]) -> str:
    """Why a patch built from `fields` would not be applied to `markdown`, with what to change ("" when it would be). This is the repairer's
    `check_patch` tool; the final gate (`apply_patches`) applies the same rules to its answer."""
    try:
        patch = agents.schemas.PagePatch(source_sha256 = source_sha256, rationale = "checked by the repairer", **fields)
    except ValidationError as error:
        return "; ".join(f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors())
    known_issues = {issue["issue_id"]: issue for issue in assessment.get("issues", [])}
    reason, _, _ = patch_rejection(markdown, source_sha256, known_issues, patch)
    if not reason:
        return ""
    detail = f" (it occurs {markdown.count(patch.target_text)} times)" if reason == "target does not occur exactly once" else ""
    return f"{reason}{detail}. {params.REJECTION_HINTS.get(reason, '')}".strip()


def apply_patches(
    markdown: str,
    source_sha256: str,
    assessment: dict[str, Any],
    value: object,
) -> tuple[str, dict[str, Any]]:
    batch = agents.schemas.PagePatchBatch.model_validate(value)
    known_issues = {issue["issue_id"]: issue for issue in assessment["issues"]}
    accepted = []
    rejected = []
    spans = []

    for patch in batch.patches:
        reason, start, end = patch_rejection(markdown, source_sha256, known_issues, patch)
        if not reason and any(start < previous_end and end > previous_start for previous_start, previous_end in spans):
            reason = "overlapping patch target"

        if reason:
            rejected.append({
                "issue_id": patch.issue_id,
                "reason": reason,
                "proposal": patch.model_dump(mode = "json"),
            })
            continue

        spans.append((start, end))
        accepted.append((start, end, patch))

    normalized = markdown
    for start, end, patch in sorted(accepted, key = lambda item: item[0], reverse = True):
        normalized = normalized[:start] + patch.replacement + normalized[end:]

    report = {
        "accepted": [patch.model_dump(mode = "json") for _, _, patch in accepted],
        "rejected": rejected,
    }
    return normalized, report


def output_title(
    markdown: str,
    slug: str,
    assessment: dict[str, Any],
    *,
    fallback: Callable[[str, str], str],
) -> str:
    # The auditor is told to quote the title from the page, and some quote the heading line with its `# ` marker.
    proposed = re.sub(r"^#{1,6}\s+", "", str(assessment.get("title") or "").strip()).strip()
    confidence = float(assessment.get("title_confidence") or 0.0)
    if (
        proposed
        and confidence >= params.MIN_TITLE_CONFIDENCE
        and len(proposed) <= params.MAX_TITLE_CHARS
        and proposed.casefold() in markdown.casefold()
        and "\n" not in proposed
        and "\r" not in proposed
        and not any(ord(character) < 32 for character in proposed)
    ):
        return proposed
    return fallback(markdown, slug)


def overlaps_fenced_code(markdown: str, start: int, end: int) -> bool:
    """Conservatively protect fenced code, including opening and closing lines. A fence counts at any indentation:
    MDX sources nest code inside components (`<Tab>`, `<Step>`), where the fence sits four or more spaces in."""
    offset = 0
    opening: tuple[str, int, int] | None = None
    for line in markdown.splitlines(keepends = True):
        stripped = line.lstrip(" \t")
        fence = re.match(r"(`{3,}|~{3,})", stripped)
        if opening is None and fence:
            opening = (fence.group(1)[0], len(fence.group(1)), offset)
            line_start, line_end = offset, offset + len(line)
            if start < line_end and end > line_start:
                return True
        elif opening is not None:
            marker, marker_length, _open_offset = opening
            close = re.match(
                rf"{re.escape(marker)}{{{marker_length},}}\s*$",
                stripped.rstrip("\r\n"),
            )
            line_start, line_end = offset, offset + len(line)
            if start < line_end and end > line_start:
                return True
            if close:
                opening = None
        offset += len(line)

    return opening is not None and start < len(markdown) and end > opening[2]
