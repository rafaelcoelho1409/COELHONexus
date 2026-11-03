"""Offline checks for Book Distiller agent-boundary schemas.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_agent_schemas.py
"""
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.bd.agents.schemas import (  # noqa: E402
    EvidenceRef,
    EvidenceSource,
    IssueCode,
    IssueSeverity,
    PageAssessment,
    PageIssue,
    PageKind,
    PatchProposal,
    PatchRisk,
    ReviewDecision,
    ReviewDisposition,
    SpecialistRole,
    WorkIssueRef,
    WorkItem,
    WorkPlan,
)


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def rejects(label: str, build) -> None:
    try:
        build()
    except ValidationError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected Pydantic validation to fail")


def evidence(page_number: int, excerpt: str) -> EvidenceRef:
    return EvidenceRef(
        source = EvidenceSource.PAGE_MARKDOWN,
        excerpt = excerpt,
        page_number = page_number,
    )


def issue(page_number: int, code: IssueCode, excerpt: str) -> PageIssue:
    return PageIssue(
        issue_id = f"p{page_number:04d}-i1",
        code = code,
        severity = IssueSeverity.MEDIUM,
        description = f"Candidate {code.value} issue",
        confidence = 0.9,
        evidence = [evidence(page_number, excerpt)],
    )


BLANK_PAGES = (2, 4, 8, 10, 16, 78, 106, 126, 178, 200, 232, 236, 262)
blank_assessments = [
    PageAssessment(page_number = page, page_kind = PageKind.BLANK, confidence = 0.99)
    for page in BLANK_PAGES
]
check(
    "all 13 audited blank pages remain represented as physical page assessments",
    tuple(assessment.page_number for assessment in blank_assessments),
    BLANK_PAGES,
)
image_only = PageAssessment(page_number = 229, page_kind = PageKind.IMAGE_ONLY, confidence = 0.98)
check("the audited image-only page has a distinct classification", image_only.page_kind, PageKind.IMAGE_ONLY)

known_issues = [
    (3, IssueCode.CONTROL_CHARACTERS, "reviewer names"),
    (5, IssueCode.SPLIT_WORD, "Invest igator's"),
    (37, IssueCode.SOFT_HYPHEN, "cyber\u00ad attacks"),
    (127, IssueCode.SOFT_HYPHEN, "trade\u00ad craft"),
    (9, IssueCode.TOC_NOISE, "Contents"),
    (189, IssueCode.FUSED_MARKER, "1src"),
    (214, IssueCode.FUSED_MARKER, "signatures 1 detect"),
    (263, IssueCode.TABLE_OR_INDEX_LAYOUT, "I NDE X"),
]
assessments = [
    PageAssessment(
        page_number = page_number,
        page_kind = PageKind.TEXT,
        confidence = 0.9,
        issues = [issue(page_number, code, excerpt)],
    )
    for page_number, code, excerpt in known_issues
]
check("known audited irregularities validate with page-local evidence", len(assessments), len(known_issues))

text_repair_work = WorkItem(
    role = SpecialistRole.TEXT_REPAIR,
    issue_refs = [
        WorkIssueRef(issue_id = "p0005-i1", page_number = 5, issue_code = IssueCode.SPLIT_WORD),
        WorkIssueRef(issue_id = "p0037-i1", page_number = 37, issue_code = IssueCode.SOFT_HYPHEN),
    ],
    evidence = [evidence(5, "Invest igator's"), evidence(37, "cyber\u00ad attacks")],
    rationale = "Review the exact extracted spans against their cited source evidence.",
)
work_plan = WorkPlan(work_items = [text_repair_work])
check("a work plan assigns bounded evidence-backed work to an allowed specialist",
      work_plan.work_items[0].role, SpecialistRole.TEXT_REPAIR)
check("an empty work plan represents no specialist work", WorkPlan().work_items, [])
rejects(
    "a work-item issue reference requires a positive page number",
    lambda: WorkItem(
        role = SpecialistRole.TEXT_REPAIR,
        issue_refs = [WorkIssueRef(
            issue_id = "p0000-i1",
            page_number = 0,
            issue_code = IssueCode.SPLIT_WORD,
        )],
        evidence = [evidence(1, "Invest igator's")],
        rationale = "Review a split word.",
    ),
)
rejects(
    "a work item cannot repeat an issue reference",
    lambda: WorkItem(
        role = SpecialistRole.TEXT_REPAIR,
        issue_refs = [
            WorkIssueRef(issue_id = "p0005-i1", page_number = 5, issue_code = IssueCode.SPLIT_WORD),
            WorkIssueRef(issue_id = "p0005-i1", page_number = 5, issue_code = IssueCode.SPLIT_WORD),
        ],
        evidence = [evidence(5, "Invest igator's")],
        rationale = "Review a split word.",
    ),
)
rejects(
    "specialist roles cannot be assigned unsupported issue categories",
    lambda: WorkItem(
        role = SpecialistRole.METADATA,
        issue_refs = [
            WorkIssueRef(issue_id = "p0005-i1", page_number = 5, issue_code = IssueCode.SPLIT_WORD),
        ],
        evidence = [evidence(5, "Invest igator's")],
        rationale = "This role cannot repair prose.",
    ),
)
rejects(
    "a work item is limited to 32 explicit issue references",
    lambda: WorkItem(
        role = SpecialistRole.TEXT_REPAIR,
        issue_refs = [
            WorkIssueRef(
                issue_id = f"p{page_number:04d}-i1",
                page_number = page_number,
                issue_code = IssueCode.SPLIT_WORD,
            )
            for page_number in range(1, 34)
        ],
        evidence = [evidence(1, "Invest igator's")],
        rationale = "Review a bounded range.",
    ),
)
rejects(
    "a work plan is limited to 64 work items",
    lambda: WorkPlan(work_items = [text_repair_work] * 65),
)
rejects(
    "an issue can be assigned to only one specialist work item",
    lambda: WorkPlan(work_items = [text_repair_work, text_repair_work]),
)
rejects(
    "issue identifiers must be unique within a page assessment",
    lambda: PageAssessment(
        page_number = 5,
        page_kind = PageKind.TEXT,
        confidence = 0.9,
        issues = [
            issue(5, IssueCode.SPLIT_WORD, "Invest igator's"),
            issue(5, IssueCode.SPLIT_WORD, "Invest igator's"),
        ],
    ),
)

rejects(
    "a page number must be positive",
    lambda: PageAssessment(page_number = 0, page_kind = PageKind.TEXT, confidence = 0.5),
)
rejects(
    "confidence must be within the closed zero-to-one interval",
    lambda: PageAssessment(page_number = 1, page_kind = PageKind.TEXT, confidence = 1.01),
)
rejects(
    "unknown fields are rejected at the model boundary",
    lambda: PageAssessment(page_number = 1, page_kind = PageKind.TEXT, confidence = 0.5, extra = "ignored"),
)
rejects(
    "a page issue must cite at least one evidence item",
    lambda: PageIssue(
        code = IssueCode.TEXT_CORRUPTION,
        severity = IssueSeverity.HIGH,
        description = "Unsubstantiated issue",
        confidence = 0.9,
        evidence = [],
    ),
)

patch_values = {
    "page_number": 5,
    "issue_id": "p0005-i1",
    "source_sha256": "a" * 64,
    "issue_code": IssueCode.SPLIT_WORD,
    "risk": PatchRisk.LOW,
    "context_before": "The ",
    "target_text": "Invest igator's",
    "context_after": " Guide",
    "replacement": "Investigator's",
    "rationale": "The title page supports the unbroken word.",
    "confidence": 0.95,
    "evidence": [evidence(5, "Invest igator's")],
}
patch = PatchProposal(**patch_values)
check("a patch proposal is tied to a lowercase SHA-256 source version", len(patch.source_sha256), 64)
rejects(
    "patches cannot be no-ops",
    lambda: PatchProposal(**{**patch_values, "replacement": patch_values["target_text"]}),
)
rejects(
    "patches reject malformed source hashes",
    lambda: PatchProposal(**{**patch_values, "source_sha256": "z" * 64}),
)
rejects(
    "patches require evidence",
    lambda: PatchProposal(**{**patch_values, "evidence": []}),
)

decision = ReviewDecision(
    proposal_id = "proposal-1",
    disposition = ReviewDisposition.EDIT,
    rationale = "Keep the possessive punctuation from the source.",
    edited_replacement = "Investigator's",
)
check("an edit review carries the revised replacement", decision.edited_replacement, "Investigator's")
rejects(
    "an edit decision requires an edited replacement",
    lambda: ReviewDecision(
        proposal_id = "proposal-1",
        disposition = ReviewDisposition.EDIT,
        rationale = "Change the proposal.",
    ),
)
rejects(
    "non-edit decisions cannot carry an edited replacement",
    lambda: ReviewDecision(
        proposal_id = "proposal-1",
        disposition = ReviewDisposition.ACCEPT,
        rationale = "Accept as proposed.",
        edited_replacement = "unexpected",
    ),
)

check("assessment schema exports as JSON Schema for later structured-output integration",
      "page_kind" in PageAssessment.model_json_schema()["properties"], True)
check("work-plan JSON Schema exposes its item bound",
      WorkPlan.model_json_schema()["properties"]["work_items"]["maxItems"], 64)
