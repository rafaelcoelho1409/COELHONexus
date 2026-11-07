"""Deterministic allowlisted routing from audit issues to specialist roles."""
from __future__ import annotations
from .... import agents, graphs


# What a specialist may be assigned is what it may patch, less the findings code fixes by itself (repair_pages removes
# control characters and soft hyphens), so no specialist is asked about those.
TEXT_ISSUES = (
    agents.schemas.ALLOWED_ISSUES_BY_ROLE[agents.schemas.SpecialistRole.TEXT_REPAIR]
    - agents.schemas.MECHANICAL_ISSUES
)
STRUCTURE_ISSUES = (
    agents.schemas.ALLOWED_ISSUES_BY_ROLE[agents.schemas.SpecialistRole.STRUCTURE]
    - agents.schemas.MECHANICAL_ISSUES
)


def plan_repairs(state: graphs.ingestion.states.IngestionRunState) -> dict:
    """Create page-local assignments; metadata is handled by its dedicated stage."""
    assessments = state.get("page_audits")
    if not isinstance(assessments, list) or len(assessments) != state.get("expected_page_count"):
        raise ValueError("repair planning requires a complete page audit")

    work_items = []
    for page in assessments:
        assessment = agents.schemas.PageAssessment.model_validate(page["assessment"])
        for role, issue_codes in (
            (agents.schemas.SpecialistRole.TEXT_REPAIR, TEXT_ISSUES),
            (agents.schemas.SpecialistRole.STRUCTURE, STRUCTURE_ISSUES),
        ):
            selected = [issue for issue in assessment.issues if issue.code in issue_codes]
            if not selected:
                continue
            item = agents.schemas.WorkItem(
                role = role,
                issue_refs = [agents.schemas.WorkIssueRef(
                    issue_id = issue.issue_id,
                    page_number = assessment.page_number,
                    issue_code = issue.code,
                ) for issue in selected],
                evidence = [evidence for issue in selected for evidence in issue.evidence],
                rationale = f"Repair assigned {role.value} issues on page {assessment.page_number}.",
            )
            work_items.append({
                "page": assessment.page_number,
                "role": role.value,
                "issue_refs": [ref.model_dump(mode = "json") for ref in item.issue_refs],
                "evidence": [evidence.model_dump(mode = "json") for evidence in item.evidence],
                "rationale": item.rationale,
            })
    return {"work_items": work_items, "status": "planned"}
