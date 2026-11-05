"""Validation and prompt-payload construction for documentation page audits."""
from __future__ import annotations

from . import params
from .... import agents

import json


def user_payload(
    framework_slug: str,
    page_index: int,
    page_url: str,
    page_title: str,
    markdown: str,
) -> str:
    return json.dumps({
        "task": "Assess normalization quality for this one documentation page.",
        "framework_slug": framework_slug,
        "page_index": page_index,
        "page_url": page_url,
        "page_title": page_title,
        "markdown": markdown,
    }, ensure_ascii = False)


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
