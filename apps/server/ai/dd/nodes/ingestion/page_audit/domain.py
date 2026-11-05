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
    """Keep only auditor findings with evidence present in the source Markdown."""
    assessment = agents.schemas.PageAssessment.model_validate(value)
    if len(assessment.issues) > params.MAX_ISSUES_PER_PAGE:
        raise ValueError("assessment exceeds the maximum issues per page")

    accepted = []
    warnings = []
    for issue in assessment.issues:
        if len(issue.evidence) > params.MAX_EVIDENCE_CHARS or issue.evidence not in markdown:
            warnings.append(
                f"Discarded finding {issue.issue_id}: evidence was not a bounded exact source substring."
            )
            continue
        accepted.append(issue)

    return assessment.model_copy(update = {"issues": accepted}), warnings
