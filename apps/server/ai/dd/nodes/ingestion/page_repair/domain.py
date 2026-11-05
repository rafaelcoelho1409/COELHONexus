"""Source-version and evidence validation for page repair proposals."""
from __future__ import annotations

from . import params
from .... import agents

import re
from collections.abc import Callable
from typing import Any


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
        reason = ""
        if patch.source_sha256 != source_sha256:
            reason = "source hash mismatch"
        elif patch.issue_id not in known_issues:
            reason = "patch does not reference an accepted finding"
        elif patch.evidence != known_issues[patch.issue_id]["evidence"]:
            reason = "patch evidence does not match its audited finding"
        elif not (
            patch.evidence in patch.target_text
            or patch.target_text in patch.evidence
        ):
            reason = "patch target does not overlap its audited evidence"
        elif known_issues[patch.issue_id]["category"] not in params.PATCHABLE_CATEGORIES:
            reason = "finding category is not eligible for automatic repair"
        elif patch.confidence < params.MIN_PATCH_CONFIDENCE:
            reason = "confidence below the automatic-apply threshold"
        elif patch.evidence not in markdown:
            reason = "evidence is not an exact source substring"
        elif markdown.count(patch.target_text) != 1:
            reason = "target does not occur exactly once"
        else:
            start = markdown.index(patch.target_text)
            end = start + len(patch.target_text)
            if overlaps_fenced_code(markdown, start, end):
                reason = "patch target overlaps a fenced code block"
            before = markdown[max(0, start - len(patch.context_before)):start]
            after = markdown[end:end + len(patch.context_after)]
            if not reason and (before != patch.context_before or after != patch.context_after):
                reason = "surrounding context mismatch"
            elif not reason and any(
                start < previous_end and end > previous_start
                for previous_start, previous_end in spans
            ):
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
    proposed = str(assessment.get("title") or "").strip()
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
    """Conservatively protect fenced code, including opening and closing lines."""
    offset = 0
    opening: tuple[str, int, int] | None = None
    for line in markdown.splitlines(keepends = True):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        fence = re.match(r"(`{3,}|~{3,})", stripped) if indent <= 3 else None
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
