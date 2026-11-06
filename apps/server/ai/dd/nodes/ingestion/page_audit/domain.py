"""Validation and prompt-payload construction for documentation page audits."""
from __future__ import annotations

from . import params
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
        if issue.category in params.CONVENTION_CATEGORIES and agents.ingestion.page_auditor.patterns.EXPORT_CONVENTION_RE.match(issue.evidence):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence is a documentation-export convention "
                "(JSX component, HTML media tag or fence attribute), not a page defect."
            )
            continue
        if issue.category in params.DUPLICATE_CATEGORIES and not is_repeat(markdown, issue.evidence, issue.other_evidence):
            warnings.append(
                f"Discarded finding {issue.issue_id}: its evidence occurs once on the page and no second copy was found, so nothing is repeated."
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
