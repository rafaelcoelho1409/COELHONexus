"""Create page-local specialist assignments without importing agent or graph contracts."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_MECHANICAL_ISSUES = frozenset({"control_characters", "soft_hyphen"})
_ISSUES_BY_ROLE = {
    "text_repair": frozenset({
        "control_characters",
        "split_word",
        "soft_hyphen",
        "fused_marker",
        "text_corruption",
    }),
    "structure": frozenset({"structure", "toc_noise", "table_or_index_layout"}),
}
_KNOWN_ISSUES = frozenset({
    "control_characters",
    "near_empty",
    "split_word",
    "soft_hyphen",
    "toc_noise",
    "fused_marker",
    "text_corruption",
    "structure",
    "table_or_index_layout",
    "metadata",
    "other",
})


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def plan_repairs(state: Mapping[str, Any]) -> dict[str, Any]:
    """Route supported audit findings, skipping pages excluded from the processed book."""
    assessments = state.get("page_audits")
    expected_page_count = state.get("expected_page_count")
    if not isinstance(assessments, list) or len(assessments) != expected_page_count:
        raise ValueError("repair planning requires a complete page audit")
    selection = state.get("content_selection")
    if not isinstance(selection, list) or len(selection) != expected_page_count:
        raise ValueError("repair planning requires a complete early content selection")
    excluded_pages = {
        page["page_number"] for page in selection
        if isinstance(page, dict) and page.get("disposition") == "exclude"
    }

    work_items = []
    for page in assessments:
        if not isinstance(page, dict) or type(page.get("page")) is not int:
            raise ValueError("repair planning received a malformed page audit")
        page_number = page["page"]
        if page_number in excluded_pages:
            continue
        assessment = page.get("assessment")
        if not isinstance(assessment, dict):
            raise ValueError(f"page {page_number} has no valid page assessment")
        if type(assessment.get("page_number")) is not int or assessment["page_number"] != page_number:
            raise ValueError(f"page {page_number} assessment has a mismatched page number")
        issues = assessment.get("issues")
        if not isinstance(issues, list):
            raise ValueError(f"page {page_number} assessment has no issue list")
        issue_ids = [issue.get("issue_id") for issue in issues if isinstance(issue, dict)]
        if (
            len(issue_ids) != len(issues)
            or any(not isinstance(issue_id, str) or not issue_id for issue_id in issue_ids)
            or len(issue_ids) != len(set(issue_ids))
        ):
            raise ValueError(f"page {page_number} assessment has invalid or duplicate issue identities")

        for role, allowed_codes in _ISSUES_BY_ROLE.items():
            selected = []
            for issue in issues:
                if not isinstance(issue, dict):
                    raise ValueError(f"page {page_number} contains a malformed issue")
                issue_code = _value(issue.get("code"))
                if not isinstance(issue_code, str) or issue_code not in _KNOWN_ISSUES:
                    raise ValueError(f"page {page_number} contains an unknown issue code")
                if issue_code in allowed_codes and issue_code not in _MECHANICAL_ISSUES:
                    selected.append(issue)
            if not selected:
                continue

            issue_refs = []
            evidence = []
            for issue in selected:
                issue_id = issue.get("issue_id")
                issue_code = _value(issue.get("code"))
                if not isinstance(issue_id, str) or not issue_id or not isinstance(issue_code, str):
                    raise ValueError(f"page {page_number} contains an issue without a valid identity")
                issue_refs.append({
                    "issue_id": issue_id,
                    "page_number": page_number,
                    "issue_code": issue_code,
                })
                refs = issue.get("evidence")
                if not isinstance(refs, list) or not refs:
                    raise ValueError(f"issue {issue_id} has no evidence")
                for ref in refs:
                    if hasattr(ref, "model_dump"):
                        ref = ref.model_dump(mode = "json")
                    if not isinstance(ref, dict) or not isinstance(ref.get("source"), str) or not isinstance(
                        ref.get("excerpt"), str
                    ) or not ref["excerpt"]:
                        raise ValueError(f"issue {issue_id} contains malformed evidence")
                    evidence.append(ref)

            work_items.append({
                "page": page_number,
                "role": role,
                "issue_refs": issue_refs,
                "evidence": evidence,
                "rationale": f"Repair assigned {role} issues on page {page_number}.",
            })
    return {"work_items": work_items, "status": "planned"}
