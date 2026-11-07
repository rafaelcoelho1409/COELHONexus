"""Offline checks for DD page-agent retries, timeouts, and page scheduling.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_page_agents_retry.py
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx
from openai import APITimeoutError

from ai.dd.agents import schemas
from ai.dd.agents.ingestion.page_auditor import config as auditor_config
from ai.dd.agents.ingestion.page_auditor import domain as auditor_domain
from ai.dd.agents.ingestion.page_auditor import params as auditor_params
from ai.dd.agents.ingestion.page_auditor import patterns as auditor_patterns
from ai.dd.agents.ingestion.page_repair import config as repair_config
from ai.dd.nodes.ingestion import audit_batch, page_audit, page_repair
from domains.settings.chat import service as chat_service


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


class FakeAgent:
    """Answers each call from `responses`; the last one repeats."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, payload, config = None):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, BaseException):
            raise response
        return response


class FakeProviderError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"provider returned {status_code}")
        self.status_code = status_code


page = {"idx": 19, "url": "https://docs.test/llms-full.txt", "title": "Kubernetes Operator"}
markdown = "# Kubernetes Operator\n\nRuns Locust on a cluster."
assessment = schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9)
batch = schemas.PagePatchBatch()
attempts = page_audit.params.MAX_ATTEMPTS
repair_attempts = page_repair.params.MAX_ATTEMPTS  # an optional step: fewer tries than the audit

check("both page agents use a bounded 120-second request with SDK retries disabled",
      (auditor_config.MODEL.timeout_s, repair_config.MODEL.timeout_s,
       auditor_config.MODEL.max_retries, repair_config.MODEL.max_retries),
      (120.0, 120.0, 0, 0))


async def audit(agent):
    return await page_audit.node.audit_page("locust", page, markdown, agent)


async def repair(agent):
    return await page_repair.node.repair_page(
        "locust", page, markdown, "0" * 64, {"issues": []}, agent,
    )


async def audit_batch_order() -> tuple[list[int], list[int]]:
    order = []
    bodies = {}
    pages = []
    for idx, size in ((1, 40), (2, 120), (3, 80)):
        source_key = f"source/{idx}"
        body = f"# Page {idx}\n\nBody {idx}."
        bodies[source_key] = body
        pages.append({
            "idx": idx,
            "slug": f"page-{idx}",
            "url": f"https://docs.test/{idx}",
            "tier": "docs",
            "title": f"Page {idx}",
            "section": "",
            "notes": "",
            "lastmod": "",
            "anchors": [],
            "bytes": size,
            "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_key": source_key,
            "raw_key": source_key,
        })

    class OrderedAgent:
        async def ainvoke(self, payload, config = None):
            order.append(json.loads(payload["messages"][0]["content"])["page_index"])
            return {"structured_response": assessment}

    async def read_page(source_key):
        return bodies[source_key]

    async def artifact_exists(key):
        return False

    async def read_artifact(key):
        raise AssertionError("no prior audit artifact should be read")

    async def write_artifact(key, value, content_type):
        return None

    async def page_done():
        return None

    state = {"framework_slug": "locust", "run_id": "run-1", "pages": pages}
    concurrency = audit_batch.params.PAGE_CONCURRENCY
    audit_batch.params.PAGE_CONCURRENCY = 1
    try:
        result = await audit_batch.service.audit_pages(
            state,
            read_page,
            artifact_exists,
            read_artifact,
            write_artifact,
            OrderedAgent(),
            None,
            page_done,
        )
    finally:
        audit_batch.params.PAGE_CONCURRENCY = concurrency
    return order, [item["idx"] for item in result["assessments"]]


# --- page auditor
good = FakeAgent({"structured_response": assessment})
result, warnings = asyncio.run(audit(good))
check("an auditor that answers first time is asked once", good.calls, 1)
check("its assessment is returned", result.page_kind, schemas.PageKind.DOCUMENTATION)

slips = FakeAgent({"structured_response": None}, {"messages": []}, {"structured_response": assessment})
result, warnings = asyncio.run(audit(slips))
check("an empty or missing structured response is asked again", slips.calls, 3)
check("the answer that follows is used", result.confidence, 0.9)

never = FakeAgent({"structured_response": None})
rejects("an auditor that never answers fails", lambda: asyncio.run(audit(never)))
check("it is asked as many times as allowed, no more", never.calls, attempts)

waits = []
real_sleep = chat_service.asyncio.sleep


async def capture_sleep(delay):
    waits.append(delay)


chat_service.asyncio.sleep = capture_sleep
try:
    recovers = FakeAgent(FakeProviderError(504), FakeProviderError(429), {"structured_response": assessment})
    result, _ = asyncio.run(audit(recovers))
    check("the auditor retries 5xx and 429 responses, then accepts a structured result", recovers.calls, 3)
    check("transient retries use 20-second then 40-second pauses", waits, [20.0, 40.0])
    check("the auditor returns the recovered result", result.page_kind, schemas.PageKind.DOCUMENTATION)

    waits.clear()
    timeout_error = APITimeoutError(request = httpx.Request("POST", "https://rotator.test"))
    repairs = FakeAgent(timeout_error, {"structured_response": batch})
    asyncio.run(repair(repairs))
    check("the repairer retries a timeout once", (repairs.calls, waits), (2, [20.0]))

    waits.clear()
    bad_request = FakeAgent(FakeProviderError(400), {"structured_response": assessment})
    try:
        asyncio.run(audit(bad_request))
    except FakeProviderError as error:
        check(
            "non-transient provider errors are passed through without retry",
            (error.status_code, bad_request.calls),
            (400, 1),
        )
    else:
        raise AssertionError("a non-transient provider error should be raised")

    waits.clear()
    exhausted = FakeAgent(FakeProviderError(503))
    try:
        asyncio.run(audit(exhausted))
    except FakeProviderError as error:
        check(
            "transient failures stop at the configured attempt limit",
            (error.status_code, exhausted.calls),
            (503, attempts),
        )
    else:
        raise AssertionError("an exhausted transient failure should be raised")
    check("the exhausted page uses bounded backoff", waits, [20.0, 40.0])
finally:
    chat_service.asyncio.sleep = real_sleep

order, result_order = asyncio.run(audit_batch_order())
check(
    "audits start largest first and return in source-index order",
    (order, result_order),
    ([2, 3, 1], [1, 2, 3]),
)

# --- page repair specialist
good = FakeAgent({"structured_response": batch})
asyncio.run(repair(good))
check("a repairer that answers first time is asked once", good.calls, 1)

slips = FakeAgent({"structured_response": None}, {"structured_response": batch})
asyncio.run(repair(slips))
check("a repairer with an empty structured response is asked again", slips.calls, 2)

never = FakeAgent({"structured_response": None})
rejects("a repairer that never answers fails", lambda: asyncio.run(repair(never)))
check("it is asked as many times as allowed, no more", never.calls, repair_attempts)
check("a repair is allowed fewer tries than an audit", repair_attempts < attempts, True)

# --- repair eligibility, finding dedupe, and a repairer that cannot be reached
from ai.dd.nodes.ingestion import normalize_pages


def finding(issue_id: str, category: str, confidence: float, evidence: str = "(\\*\\*kwargs)") -> dict:
    return {
        "issue_id": issue_id, "category": category, "severity": "low",
        "description": "d", "evidence": evidence, "confidence": confidence,
    }


mixed = {"page_kind": "documentation", "issues": [
    finding("a", "markdown_formatting", 0.8),
    finding("b", "other", 0.95),
    finding("c", "malformed_heading", 0.9),
]}
check(
    "only a patchable category at or above the patch confidence reaches the repairer",
    [issue["issue_id"] for issue in page_repair.domain.repairable_assessment(mixed)["issues"]],
    ["c"],
)

source = "Prose (\\*\\*kwargs) and more prose."
raw = schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [
    schemas.PageIssue(issue_id = "p1", category = "markdown_formatting", severity = "low",
                      description = "d", evidence = "(\\*\\*kwargs)", confidence = 0.8),
    schemas.PageIssue(issue_id = "p2", category = "markdown_formatting", severity = "low",
                      description = "d", evidence = "(\\*\\*kwargs)", confidence = 0.92),
    schemas.PageIssue(issue_id = "p3", category = "malformed_heading", severity = "low",
                      description = "d", evidence = "(\\*\\*kwargs)", confidence = 0.8),
])
deduped, notes = page_audit.domain.sanitize_assessment(raw, markdown = source)
check(
    "findings on the same text and category merge into the most confident one",
    [(issue.issue_id, issue.confidence) for issue in deduped.issues],
    [("p2", 0.92), ("p3", 0.8)],
)
check("a merge is reported as a warning", len(notes), 1)


async def normalize_with(agent, issues: list[dict]) -> tuple[dict, dict]:
    body = "# Hooks\n\nUse (\\*\\*kwargs) here."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    page_ref = {
        "idx": 14, "slug": "event-hooks", "url": "https://docs.test/hooks", "tier": "docs",
        "title": "Hooks", "section": "", "notes": "", "lastmod": "", "anchors": [],
        "bytes": len(body), "source_sha256": sha, "source_key": "src/14", "raw_key": "raw/14",
    }
    written = {}

    async def read_page(key):
        return body

    async def artifact_exists(key):
        return False

    async def read_artifact(key):
        raise AssertionError("nothing was saved")

    async def write_artifact(key, value, content_type):
        written[key] = value

    async def page_done():
        return None

    state = {
        "framework_slug": "locust", "run_id": "run-1", "pages": [page_ref],
        "assessments": [{
            "idx": 14, "source_sha256": sha,
            "assessment": {"page_kind": "documentation", "issues": issues},
        }],
    }
    result = await normalize_pages.service.normalize_pages(
        state, read_page, artifact_exists, read_artifact, write_artifact, agent, None, page_done,
    )
    saved = json.loads(next(v for k, v in written.items() if "assessments/" in k))
    return result["output_pages"][0], saved


real_sleep = chat_service.asyncio.sleep
chat_service.asyncio.sleep = capture_sleep
try:
    low_only = FakeAgent({"structured_response": batch})
    output, saved = asyncio.run(normalize_with(low_only, [finding("a", "markdown_formatting", 0.8)]))
    check("a page whose findings cannot be patched never calls the repairer", low_only.calls, 0)
    check("it is recorded as not needing repair", (output["repair_status"], saved["repair"]["eligible_issues"]), ("not_needed", 0))

    silent = FakeAgent({"structured_response": None})
    output, saved = asyncio.run(normalize_with(silent, [finding("c", "malformed_heading", 0.95)]))
    check("a repairer that never answers still completes the page", saved["normalization_status"], "complete")
    check("the page keeps its audited original", output["bytes"], len("# Hooks\n\nUse (\\*\\*kwargs) here.".encode("utf-8")))
    check("the failure is recorded for the run summary", output["repair_status"], "unavailable")
    check("the saved audit carries the failure reason", saved["repair"]["error"].startswith("ValueError"), True)
    check("it was asked as many times as a repair is allowed", silent.calls, repair_attempts)
finally:
    chat_service.asyncio.sleep = real_sleep

# --- an answer written as plain JSON instead of the structured-output tool call
from langchain_core.messages import AIMessage, ToolMessage

ASSESSMENT_JSON = '{"page_kind": "documentation", "confidence": 0.95, "title": "Hooks", "issues": []}'


def prose(text: str) -> dict:
    return {"messages": [AIMessage(content = text)]}


fenced = FakeAgent(prose("Based on my analysis, here is the PageAssessment:\n\n```json\n" + ASSESSMENT_JSON + "\n```"))
result, _ = asyncio.run(audit(fenced))
check("a fenced JSON answer is used as the assessment, without asking again", (fenced.calls, result.title), (1, "Hooks"))

after_tool = FakeAgent({"messages": [
    AIMessage(content = "", tool_calls = [{"name": "read_file", "args": {}, "id": "1"}]),
    ToolMessage(content = "skill text with {braces}", tool_call_id = "1"),
    AIMessage(content = ASSESSMENT_JSON),
]})
result, _ = asyncio.run(audit(after_tool))
check("only the last assistant message is read", (after_tool.calls, result.confidence), (1, 0.95))

wrong_shape = FakeAgent(prose('```json\n{"verdict": "fine"}\n```'), {"structured_response": assessment})
asyncio.run(audit(wrong_shape))
check("JSON that does not fit the schema is asked again", wrong_shape.calls, 2)

patch_json = FakeAgent(prose('Here is the PagePatchBatch:\n```json\n{"patches": []}\n```'))
asyncio.run(repair(patch_json))
check("a fenced PagePatchBatch is used, without asking again", patch_json.calls, 1)


async def audit_pages_with(agent) -> tuple[dict, dict]:
    body = "# Hooks\n\nBody."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    page_ref = {
        "idx": 10, "slug": "hooks", "url": "https://docs.test/hooks", "tier": "docs", "title": "Hooks",
        "section": "", "notes": "", "lastmod": "", "anchors": [], "bytes": len(body),
        "source_sha256": sha, "source_key": "src/10", "raw_key": "raw/10",
    }
    written = {}

    async def read_page(key):
        return body

    async def artifact_exists(key):
        return False

    async def read_artifact(key):
        raise AssertionError("nothing was saved")

    async def write_artifact(key, value, content_type):
        written[key] = value

    async def page_done():
        return None

    state = {"framework_slug": "locust", "run_id": "run-1", "pages": [page_ref]}
    result = await audit_batch.service.audit_pages(
        state, read_page, artifact_exists, read_artifact, write_artifact, agent, None, page_done,
    )
    return result["assessments"][0], json.loads(next(iter(written.values())))


unanswering = FakeAgent({"structured_response": None})
record, saved = asyncio.run(audit_pages_with(unanswering))
check("an auditor that never answers still completes the audit", record["audit_status"], "unavailable")
check("it was asked as many times as allowed", unanswering.calls, attempts)
check("the saved record carries the reason and no findings", (saved["audit_error"].startswith("ValueError"), saved["assessment"]["issues"]), (True, []))

answering = FakeAgent({"structured_response": assessment})
record, saved = asyncio.run(audit_pages_with(answering))
check("a page that is audited is marked completed", record["audit_status"], "completed")

# --- patch guard: a formatting repair never drops words; a quoted heading marker never reaches the title
SOURCE = "# Notes\n\n#### NOTE\nBecause it matters.\n\nSee [Troubleshooting Installation]().\n"
SHA = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()


def one_patch(issue_id: str, target: str, replacement: str, before: str, after: str, category: str = "markdown_formatting"):
    issues = [{"issue_id": issue_id, "category": category, "severity": "low", "description": "d",
               "evidence": target, "confidence": 0.95}]
    patch = schemas.PagePatch(
        issue_id = issue_id, source_sha256 = SHA, context_before = before, target_text = target,
        context_after = after, replacement = replacement, rationale = "r", evidence = target, confidence = 0.95,
    )
    return page_repair.domain.apply_patches(SOURCE, SHA, {"issues": issues}, schemas.PagePatchBatch(patches = [patch]))


body, report = one_patch("a", "#### NOTE\nBecause it matters.", "> Because it matters.", "\n\n", "\n\nSee")
check("a formatting patch that drops a word is rejected", (body == SOURCE, report["rejected"][0]["reason"]),
      (True, "formatting patch removes words from the page"))

body, report = one_patch("b", "[Troubleshooting Installation]()", "[Troubleshooting Installation](#troubleshooting-installation)", "See ", ".\n")
check("a formatting patch that only adds a destination is applied", (len(report["accepted"]), "(#troubleshooting-installation)" in body), (1, True))

body, report = one_patch("c", "#### NOTE\nBecause it matters.", "Because it matters.", "\n\n", "\n\nSee", category = "duplicate_boilerplate")
check("a repeat removal is rejected when the page holds only one copy",
      (len(report["accepted"]), report["rejected"][0]["reason"]), (0, "removing it would leave no copy of the text"))

TWICE_SOURCE = "# T\n\nIntro.\n\nSee docs.\n\nMiddle.\n\nSee docs.\n"
twice_sha = hashlib.sha256(TWICE_SOURCE.encode("utf-8")).hexdigest()
twice_patch = schemas.PagePatch(
    issue_id = "d", source_sha256 = twice_sha, context_before = "\n\n", target_text = "Middle.\n\nSee docs.",
    context_after = "\n", replacement = "Middle.", rationale = "r", evidence = "See docs.", confidence = 0.95,
)
twice_issue = [{"issue_id": "d", "category": "duplicate_boilerplate", "severity": "low", "description": "d",
                "evidence": "See docs.", "confidence": 0.95}]
body, report = page_repair.domain.apply_patches(TWICE_SOURCE, twice_sha, {"issues": twice_issue}, schemas.PagePatchBatch(patches = [twice_patch]))
check("a repeat removal is applied when another copy stays", (len(report["accepted"]), body.count("See docs.")), (1, 1))

titled = {"title": "# aiolocust / asyncio", "title_confidence": 0.9}
check(
    "a title quoted with its heading marker is used without it",
    page_repair.domain.output_title("# aiolocust / asyncio\n\nBody", "aiolocust", titled, fallback = lambda markdown, slug: slug),
    "aiolocust / asyncio",
)

# --- fences inside MDX components, duplicate removals, export conventions, and titles of pages split from one bundle
NESTED = (
    "# Projects\n\n<Tabs>\n  <Tab title=\"Python\">\n    To delete the Project:\n\n    ```\n"
    "    # ws.delete_project(\"PROJECT ID\")\n    ```\n  </Tab>\n</Tabs>\n"
)
nested_sha = hashlib.sha256(NESTED.encode("utf-8")).hexdigest()
nested_issue = [{"issue_id": "n", "category": "markdown_formatting", "severity": "low", "description": "d",
                 "evidence": "# ws.delete_project(\"PROJECT ID\")", "confidence": 0.95}]
nested_patch = schemas.PagePatch(
    issue_id = "n", source_sha256 = nested_sha, context_before = "    ", target_text = "# ws.delete_project(\"PROJECT ID\")",
    context_after = "\n", replacement = "ws.delete_project(\"PROJECT ID\")", rationale = "r",
    evidence = "# ws.delete_project(\"PROJECT ID\")", confidence = 0.95,
)
body, report = page_repair.domain.apply_patches(NESTED, nested_sha, {"issues": nested_issue}, schemas.PagePatchBatch(patches = [nested_patch]))
check(
    "code inside a fence nested four spaces deep in a component is never edited",
    (body == NESTED, report["rejected"][0]["reason"]),
    (True, "patch target overlaps a fenced code block"),
)

SENTENCE = "Use the menu on the left to explore other questions."
once = f"# FAQ\n\n{SENTENCE}\n"
twice = f"# FAQ\n\n{SENTENCE}\n\n## More\n\n{SENTENCE}\n"
check("removing the only copy of a text is not a deduplication",
      page_repair.domain.duplicate_survives(once, SENTENCE, SENTENCE, ""), False)
check("removing one of two copies leaves the other",
      page_repair.domain.duplicate_survives(twice, SENTENCE, f"{SENTENCE}\n\n## More\n\n{SENTENCE}", f"{SENTENCE}\n\n## More\n\n"), True)

convention_page = (
    "```python theme={null}\nimport os\n```\n\n<Note>\n  A note.\n</Note>\n\n<img alt=\"\" />\n\n"
    "<Note>\n  A note.\n</Note>\n\nOnly here.\n\nplain ```python theme={null} text\n"
)


def finding_on(category: str, evidence: str, issue_id: str):
    return schemas.PageIssue(issue_id = issue_id, category = category, severity = "low", description = "d",
                             evidence = evidence, confidence = 0.9)


raw_assessment = schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [
    finding_on("markdown_formatting", "```python theme={null}\nimport os\n```", "fence"),
    finding_on("markdown_formatting", "<Note>\n  A note.\n</Note>", "note"),
    finding_on("other", "<img alt=\"\" />", "img"),
    finding_on("duplicate_boilerplate", "<Note>\n  A note.\n</Note>", "dup"),
    finding_on("duplicate_section", "Only here.", "single"),
    finding_on("markdown_formatting", "plain ```python theme={null} text", "prose"),
])
kept, notes = page_audit.domain.sanitize_assessment(raw_assessment, markdown = convention_page)
check(
    "findings that quote an export convention are dropped; a repeat finding and prose are kept",
    [issue.issue_id for issue in kept.issues],
    ["dup", "prose"],
)
check(
    "a repeat finding whose evidence occurs once is dropped as well",
    [note for note in notes if "nothing is repeated" in note],
    ["Discarded finding single: its evidence occurs once on the page and no second copy was found, so nothing is repeated."],
)
check("each dropped finding is explained", len(notes), 4)


async def titles_for(entries: list[tuple[str, str]]) -> list[str]:
    pages = []
    written = {}
    bodies = {}
    for idx, (title, source_path) in enumerate(entries):
        body = f"# {title}\nSource: {source_path}\n\nBody {idx}."
        key = f"src/{idx}"
        bodies[key] = body
        pages.append({
            "idx": idx, "slug": f"p{idx}", "url": "https://docs.test/llms-full.txt", "source_path": source_path,
            "tier": "llms_full", "title": title, "section": "", "notes": "", "lastmod": "", "anchors": [],
            "bytes": len(body), "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_key": key, "raw_key": f"raw/{idx}",
        })

    async def read_page(key):
        return bodies[key]

    async def artifact_exists(key):
        return False

    async def read_artifact(key):
        raise AssertionError("nothing was saved")

    async def write_artifact(key, value, content_type):
        written[key] = value

    async def page_done():
        return None

    state = {
        "framework_slug": "evidently", "run_id": "run-1", "pages": pages,
        "assessments": [{"idx": page["idx"], "source_sha256": page["source_sha256"],
                         "assessment": {"page_kind": "documentation", "issues": []}} for page in pages],
    }
    result = await normalize_pages.service.normalize_pages(
        state, read_page, artifact_exists, read_artifact, write_artifact, FakeAgent({"structured_response": batch}), None, page_done,
    )
    return [page["title"] for page in result["output_pages"]]


distinct = asyncio.run(titles_for([
    ("Overview", "https://docs.test/docs/platform/overview"),
    ("Overview", "https://docs.test/docs/library/overview"),
    ("Tracing", "https://docs.test/quickstart_tracing"),
]))
check("pages split from one bundle with the same title are told apart by their own address", len(set(distinct)), 3)
check("the repeats say where they live", (distinct[0].endswith("Overview"), distinct[0] != distinct[1]), (True, True))


# --- a second copy named by the auditor, the page scans, the export-convention table, and the repairer's patch check
def repeat_issue(evidence: str, other: str = ""):
    return schemas.PageIssue(issue_id = "r", category = "duplicate_section", severity = "low", description = "d",
                             evidence = evidence, other_evidence = other, confidence = 0.9)


COPY = "Run the checks before you deploy the service to production."
spaced = f"# T\n\n{COPY}\n\n## More\n\nRun the checks   before you\ndeploy the service to production.\n"
check("a copy that differs only in whitespace is a repeat when the auditor names it",
      page_audit.domain.is_repeat(spaced, COPY, "Run the checks   before you\ndeploy the service to production."), True)
check("a second copy that is not the same text is not a repeat",
      page_audit.domain.is_repeat(spaced, COPY, "## More"), False)
check("the same place named twice is not a repeat",
      page_audit.domain.is_repeat(f"# T\n\n{COPY}\n", COPY, COPY), False)
check("evidence that occurs twice needs no named copy",
      page_audit.domain.is_repeat(f"{COPY}\n\n{COPY}\n", COPY, ""), True)

FENCED = (
    "# Setup\n\n##Install\n\nRun the installer on every node of the cluster before continuing.\n\n"
    "```bash\n##NotAHeading\nÃ© inside code\n```\n\n#\n\nCaf\u00c3\u00a9 is on the menu today, as it always is.\n\n"
    "Run the installer on every node of the cluster before continuing.\n\n"
    "```\nRun the installer on every node of the cluster before continuing.\n```\n\n"
    "    ```text\n    ##Nested\n    ```\n"
)
leads = page_audit.domain.candidates(FENCED)
check("the scans find a heading without a space, an empty heading, damaged characters and a repeated block",
      sorted(lead["kind"] for lead in leads),
      ["damaged_characters", "empty_heading", "heading_without_space", "repeated_block"])
check("code inside a fence, nested or not, is never scanned",
      [lead["evidence"] for lead in leads if lead["kind"] == "heading_without_space"], ["##Install"])
check("every lead quotes the page exactly", all(lead["evidence"] in FENCED and (not lead.get("other_evidence") or lead["other_evidence"] in FENCED) for lead in leads), True)
check("leads are numbered in reading order", [lead["candidate_id"] for lead in leads], ["c1", "c2", "c3", "c4"])
check("a clean page has no leads", page_audit.domain.candidates("# Title\n\nA clean page with one paragraph of text in it.\n"), [])

check("every export convention example matches the pattern the audit drops",
      [example for _, examples in auditor_patterns.EXPORT_CONVENTIONS for example in examples
       if not auditor_patterns.EXPORT_CONVENTION_RE.match(example)], [])
rendered = auditor_domain.system_prompt()
check("the auditor prompt carries every example and no placeholder",
      (auditor_params.CONVENTIONS_MARKER in rendered, all(example in rendered for _, examples in auditor_patterns.EXPORT_CONVENTIONS for example in examples)),
      (False, True))

REPAIR_PAGE = "# T\n\nA line.\n\nSee the guide.\n\nA line.\n"
repair_assessment = {"issues": [{"issue_id": "d", "category": "duplicate_boilerplate", "severity": "low", "description": "d",
                                  "evidence": "A line.", "confidence": 0.95}]}
repair_sha = hashlib.sha256(REPAIR_PAGE.encode("utf-8")).hexdigest()


def patch_fields(**changes):
    fields = {"issue_id": "d", "evidence": "A line.", "target_text": "A line.\n\nSee the guide.", "replacement": "",
              "confidence": 0.95, "context_before": "\n\n", "context_after": "\n\nA line.\n"}
    return {**fields, **changes}


check("a patch the final gate would apply passes the check",
      page_repair.domain.check_patch(REPAIR_PAGE, repair_sha, repair_assessment, patch_fields()), "")
non_unique = page_repair.domain.check_patch(REPAIR_PAGE, repair_sha, repair_assessment, patch_fields(target_text = "A line."))
check("a target that repeats is refused with the count and what to do",
      ("occurs 2 times" in non_unique, "Extend target_text" in non_unique), (True, True))
check("a patch that deletes the only copy is refused",
      page_repair.domain.check_patch("# T\n\nA line.\n", hashlib.sha256(b"# T\n\nA line.\n").hexdigest(), repair_assessment,
                                     patch_fields(target_text = "A line.", context_before = "\n\n", context_after = "\n")).startswith("removing it would leave no copy"),
      True)
check("a patch that changes nothing is explained, not raised",
      "replacement must change target_text" in page_repair.domain.check_patch(REPAIR_PAGE, repair_sha, repair_assessment, patch_fields(replacement = "A line.\n\nSee the guide.")),
      True)
_, gate = page_repair.domain.apply_patches(
    REPAIR_PAGE, repair_sha, repair_assessment,
    schemas.PagePatchBatch(patches = [schemas.PagePatch(source_sha256 = repair_sha, rationale = "r", **patch_fields())]),
)
check("the final gate agrees with the check", (len(gate["accepted"]), len(gate["rejected"])), (1, 0))
check("every refusal the gate can give has a hint for the model",
      sorted(reason for reason in page_repair.params.REJECTION_HINTS if not page_repair.params.REJECTION_HINTS[reason].strip()), [])
