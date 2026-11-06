"""Offline checks for the read-only, schema-bound BD page auditor and the one-page audit both the full run and the evaluation use.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_page_auditor.py
"""
import asyncio
import sys
from pathlib import Path

from langchain_openai import ChatOpenAI
from deepagents.profiles.harness.harness_profiles import _get_harness_profile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains.bd.ingestion.ai  # noqa: F401  (the agent factories read its params at build time)
from ai.bd.agents import schemas
from ai.bd.agents.ingestion.page_auditor import agent as auditor_agent
from ai.bd.nodes.ingestion.audit_batch.service import audit_page


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def rejects(label: str, operation) -> None:
    try:
        operation()
    except ValueError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected ValueError")


def rejects_runtime_error(label: str, operation) -> None:
    try:
        operation()
    except RuntimeError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected RuntimeError")


class FakeAuditor:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def ainvoke(self, payload, config = None):
        self.calls.append(payload)
        return {"structured_response": self.response}


page_number = 1
page_ref = {"page": page_number, "key": "unused", "pictures": 0}
markdown = "A soft­hyphen was retained. PRIVATE_TAIL_MUST_NOT_ENTER_STATE"
evidence_excerpt = "soft­hyphen"
assessment = schemas.PageAssessment(
    page_number = page_number,
    page_kind = schemas.PageKind.TEXT,
    confidence = 0.94,
    issues = [schemas.PageIssue(
        issue_id = "p0001-soft-hyphen",
        code = schemas.IssueCode.SOFT_HYPHEN,
        severity = schemas.IssueSeverity.LOW,
        description = "The page contains a soft hyphen in running text.",
        confidence = 0.98,
        evidence = [schemas.EvidenceRef(
            source = schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = evidence_excerpt,
            page_number = page_number,
            locator = "verbatim excerpt",
        )],
    )],
)


def audit(auditor, text: str = markdown, pictures: int = 0) -> dict:
    return asyncio.run(audit_page({**page_ref, "pictures": pictures}, text, auditor, run_id = "test-run"))


auditor = FakeAuditor(assessment)
result = audit(auditor)
check("the audit returns a validated page assessment", result["assessment"].page_number, page_number)
check("a model answer that passes validation is a reviewed audit", result["audit_mode"], "llm_reviewed")
check("the auditor is asked once", len(auditor.calls), 1)
check("the auditor receives the page as untrusted structured data", auditor.calls[0]["messages"][0]["role"], "user")
check(
    "the code's own finding of the soft hyphen is kept next to the model's, once",
    [issue.code for issue in result["assessment"].issues if issue.code == schemas.IssueCode.SOFT_HYPHEN],
    [schemas.IssueCode.SOFT_HYPHEN],
)

ungrounded = assessment.model_copy(deep = True)
ungrounded.issues[0].evidence[0].excerpt = "not present in the page"
ungrounded_result = audit(FakeAuditor(ungrounded))
check(
    "findings with ungrounded evidence are dropped, the code's finding stays",
    [issue.issue_id for issue in ungrounded_result["assessment"].issues],
    ["p0001-soft_hyphen"],
)
check("dropped ungrounded evidence is reported as a warning", len(ungrounded_result["warnings"]), 2)

wrong_page = FakeAuditor(assessment.model_copy(update = {"page_number": 2}))
wrong_result = audit(wrong_page)
check("an assessment for another page is retried, then degrades to the code's findings", wrong_result["audit_mode"], "deterministic_fallback")
check("it is asked as many times as allowed", len(wrong_page.calls), domains.bd.ingestion.ai.params.PAGE_AUDIT_ATTEMPTS)
check("and the page is marked as not reviewed by the model", wrong_result["structured_output_fallback"], True)

media_contradiction = assessment.model_copy(update = {"page_kind": schemas.PageKind.IMAGE_ONLY})
media_result = audit(FakeAuditor(media_contradiction))
check("media classifications contradicting extraction metadata are reported as a warning", len(media_result["warnings"]), 1)
check("and the code's page kind is the one kept", media_result["assessment"].page_kind, schemas.PageKind.TEXT)

long_auditor = FakeAuditor(assessment)
long_result = audit(long_auditor, "x" * 30_001)
check("oversized pages are kept for review, not truncated", long_result["audit_mode"], "deterministic_limit")
check("oversized pages do not reach the model", len(long_auditor.calls), 0)

blank_auditor = FakeAuditor(assessment)
blank_result = audit(blank_auditor, "  \n")
check("a blank page is settled by the code alone", blank_result["audit_mode"], "deterministic_only")
check("a blank page does not reach the model", len(blank_auditor.calls), 0)

model = ChatOpenAI(
    model = "bd-page-auditor-tool-safety-test",
    base_url = "http://127.0.0.1:1/v1",
    api_key = "unused",
)
compiled_auditor = auditor_agent.page_auditor(model)
tool_names = set(compiled_auditor.nodes["tools"].bound._tools_by_name)
check("model names are validated and retained", auditor_agent.domain.validate_model_name(model.model_name), model.model_name)
rejects("unnamed models are rejected", lambda: auditor_agent.domain.validate_model_name(None))
rejects_runtime_error(
    "unreviewed tools are rejected",
    lambda: auditor_agent.domain.validate_tool_names({"unreviewed_tool"}),
)
check(
    "registered DeepAgent tools are excluded or the read-only skill reader",
    sorted(tool_names - auditor_agent.params.EXCLUDED_TOOLS),
    ["read_file"],
)
profile = _get_harness_profile(f"openai:{model.model_name}")
check("the general-purpose tool-using subagent is disabled", profile.general_purpose_subagent.enabled, False)
# The profile is shared by every role on this model, so it excludes nothing; the auditor's own allowlist hides the built-ins.
check("the shared profile excludes no tool", profile.excluded_tools, frozenset())
