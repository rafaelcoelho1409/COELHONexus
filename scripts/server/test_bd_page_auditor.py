"""Offline checks for the read-only, schema-bound BD page auditor.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_page_auditor.py
"""
import asyncio
import hashlib
import sys
from pathlib import Path

from langchain_openai import ChatOpenAI
from deepagents.profiles.harness.harness_profiles import _get_harness_profile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.bd.agents import schemas
from ai.bd.agents.ingestion.page_auditor import agent as auditor_agent
from ai.bd.graphs.ingestion.graph import build_page_audit_graph


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

    async def ainvoke(self, payload):
        self.calls.append(payload)
        return {"structured_response": self.response}


book_id = "book-1"
extraction_run_id = "run-1"
page_number = 1
page_key = f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0001.md"
markdown = "A soft\u00adhyphen was retained. PRIVATE_TAIL_MUST_NOT_ENTER_STATE"
evidence_excerpt = "soft\u00adhyphen"
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

read_keys = []


def read_page(key: str) -> str | None:
    read_keys.append(key)
    return markdown if key == page_key else None


auditor = FakeAuditor(assessment)
graph = build_page_audit_graph(read_page, auditor)
initial_state = {
    "book_id": book_id,
    "extraction_run_id": extraction_run_id,
    "extraction_status": "done",
    "page_ref": {"page": page_number, "key": page_key, "pictures": 0},
}
result = asyncio.run(graph.ainvoke(initial_state))

check("the graph returns a validated page assessment", result["assessment"]["page_number"], page_number)
check("the graph hashes the exact source Markdown", result["source_sha256"], hashlib.sha256(markdown.encode("utf-8")).hexdigest())
check("the graph reads only the canonical page object", read_keys, [page_key])
check("the raw Markdown is not copied into graph state", "PRIVATE_TAIL_MUST_NOT_ENTER_STATE" in repr(result), False)
check("the auditor receives the page as untrusted structured data", auditor.calls[0]["messages"][0]["role"], "user")
check("the audit returns a compact completion status", result["status"], "complete")

ungrounded = assessment.model_copy(deep = True)
ungrounded.issues[0].evidence[0].excerpt = "not present in the page"
rejects(
    "ungrounded evidence is rejected",
    lambda: asyncio.run(build_page_audit_graph(read_page, FakeAuditor(ungrounded)).ainvoke(initial_state)),
)

wrong_page = assessment.model_copy(update = {"page_number": 2})
rejects(
    "an assessment for another page is rejected",
    lambda: asyncio.run(build_page_audit_graph(read_page, FakeAuditor(wrong_page)).ainvoke(initial_state)),
)

media_contradiction = assessment.model_copy(update = {"page_kind": schemas.PageKind.IMAGE_ONLY})
rejects(
    "media classifications contradicting extraction metadata are rejected",
    lambda: asyncio.run(build_page_audit_graph(read_page, FakeAuditor(media_contradiction)).ainvoke(initial_state)),
)

invalid_key_state = {
    **initial_state,
    "page_ref": {"page": page_number, "key": "outside/pages/0001.md", "pictures": 0},
}
reads_before_invalid_key = len(read_keys)
rejects(
    "noncanonical object keys are rejected before reading",
    lambda: asyncio.run(build_page_audit_graph(read_page, auditor).ainvoke(invalid_key_state)),
)
check("invalid keys cause no storage read", len(read_keys), reads_before_invalid_key)

long_markdown = "x" * 30_001
long_read = lambda _key: long_markdown
unused_auditor = FakeAuditor(assessment)
rejects(
    "oversized pages are rejected rather than silently truncated",
    lambda: asyncio.run(build_page_audit_graph(long_read, unused_auditor).ainvoke(initial_state)),
)
check("oversized pages do not reach the model", len(unused_auditor.calls), 0)

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
check("all registered DeepAgent tools are from the explicitly excluded set", sorted(tool_names - auditor_agent.params.EXCLUDED_TOOLS), [])
profile = _get_harness_profile(f"openai:{model.model_name}")
check("the general-purpose tool-using subagent is disabled", profile.general_purpose_subagent.enabled, False)
check("DeepAgents hides every built-in tool from the model", profile.excluded_tools, auditor_agent.params.EXCLUDED_TOOLS)
