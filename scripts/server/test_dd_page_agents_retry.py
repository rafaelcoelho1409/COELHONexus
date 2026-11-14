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

slips = FakeAgent({"structured_response": None}, {"structured_response": assessment})
result, warnings = asyncio.run(audit(slips))
check("an empty structured response is retried within the configured attempt budget", slips.calls, attempts)
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
    recovers = FakeAgent(FakeProviderError(504), {"structured_response": assessment})
    result, _ = asyncio.run(audit(recovers))
    check("the auditor retries a 5xx response and accepts a structured result", recovers.calls, 2)
    check("the single transient retry uses the configured short pause", waits, [3.0])
    check("the auditor returns the recovered result", result.page_kind, schemas.PageKind.DOCUMENTATION)

    waits.clear()
    recovers_429 = FakeAgent(FakeProviderError(429), {"structured_response": assessment})
    asyncio.run(audit(recovers_429))
    check("the auditor retries a 429 response within its attempt budget", recovers_429.calls, 2)
    check("the 429 retry also uses the configured short pause", waits, [3.0])

    waits.clear()
    timeout_error = APITimeoutError(request = httpx.Request("POST", "https://rotator.test"))
    repairs = FakeAgent(timeout_error, {"structured_response": batch})
    try:
        asyncio.run(repair(repairs))
    except APITimeoutError:
        pass
    else:
        raise AssertionError("the optional repair timeout should be surfaced after its one attempt")
    check("an optional repair does not spend a second call after timeout", (repairs.calls, waits), (1, []))

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
    check("the exhausted page uses bounded backoff", waits, [3.0])
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

slips = FakeAgent({"structured_response": None})
rejects("an optional repair with invalid output ends after one attempt", lambda: asyncio.run(repair(slips)))
check("the optional repair attempt budget is one", slips.calls, 1)

never = FakeAgent({"structured_response": None})
rejects("a repairer that never answers fails", lambda: asyncio.run(repair(never)))
check("it is asked as many times as allowed, no more", never.calls, repair_attempts)
check("an optional repair is allowed fewer tries than an audit", repair_attempts < attempts, True)

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
    finding("c", "malformed_heading", 0.95),
]}
check(
    "only a patchable category at or above the audit-confidence threshold reaches the repairer",
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
check("duplicate findings are review-only",
      (len(report["accepted"]), report["rejected"][0]["reason"]),
      (0, "finding category is not eligible for automatic repair"))

TWICE_SOURCE = "# T\n\nIntro.\n\nSee docs.\n\nMiddle.\n\nSee docs.\n"
twice_sha = hashlib.sha256(TWICE_SOURCE.encode("utf-8")).hexdigest()
twice_patch = schemas.PagePatch(
    issue_id = "d", source_sha256 = twice_sha, context_before = "\n\n", target_text = "Middle.\n\nSee docs.",
    context_after = "\n", replacement = "Middle.", rationale = "r", evidence = "See docs.", confidence = 0.95,
)
twice_issue = [{"issue_id": "d", "category": "duplicate_boilerplate", "severity": "low", "description": "d",
                "evidence": "See docs.", "confidence": 0.95}]
body, report = page_repair.domain.apply_patches(TWICE_SOURCE, twice_sha, {"issues": twice_issue}, schemas.PagePatchBatch(patches = [twice_patch]))
check("duplicate text stays untouched even when another copy remains", (len(report["accepted"]), body.count("See docs.")), (0, 2))

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

check("every export convention example is one the audit drops",
      [example for _, examples in auditor_patterns.EXPORT_CONVENTIONS for example in examples
       if not page_audit.domain.is_export_convention(example)], [])

# the Mintlify exports that auditors took for defects on the DeepAgents run: block-quote tabs, cards and callouts, and sound tables
check("a block-quote tab, card, step or callout is an export convention",
      [page_audit.domain.is_export_convention(text) for text in (
          "> [tab] StateBackend\n>\n> A thread-scoped filesystem backend.", "> [card] [View example trace](https://x.dev)", "> [step] Install",
          "> > [!WARNING]\n> >\n> > This backend grants access.", "> [!note]\n> Text")],
      [True] * 5)
check("an ordinary block quote is not",
      [page_audit.domain.is_export_convention(text) for text in ("> A quoted sentence.", "> [link text](https://x.dev) is quoted", "> ## A heading in a quote")],
      [False] * 3)
check("a table head with an alignment row or plain dashes is sound",
      [page_audit.domain.is_sound_table_head(text) for text in (
          "| Integration | Downloads |\n| :- | :- |", "| a | b | c |\n| - | - | - |", "| a | b |\n| :-: | -: |\n| 1 | 2 |", "| :- | :- |")],
      [True] * 4)
check("a table with a ragged row, no delimiter row or no pipes is not",
      [page_audit.domain.is_sound_table_head(text) for text in (
          "| a | b | c |\n| - | - |", "| a | b |\n| - | - |\n| 1 | 2 | 3 |", "| a | b |\n| 1 | 2 |", "- | -", "just text")],
      [False] * 5)
check("an escaped pipe and a pipe in a code span are not cell edges",
      page_audit.domain.is_sound_table_head("| a | b |\n| - | - |\n| x \\| y | `p|q` |"), True)
table_page = "| Integration | Downloads |\n| :- | :- |\n| A | 1 |\n"
table_findings = schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [
    finding_on("markdown_formatting", "| Integration | Downloads |\n| :- | :- |", "table"),
    finding_on("markdown_formatting", "| A | 1 |", "row"),
])
kept_tables, _ = page_audit.domain.sanitize_assessment(table_findings, markdown = table_page)
check("a finding that calls a sound table head malformed is dropped; one about a data row is kept", [issue.issue_id for issue in kept_tables.issues], ["row"])

# Cross-format false-positive guards preserve source semantics while validating quoted Markdown.
quoted_code = "> Background\n>\n> ```bash\n> ##Install\n> echo safe\n> ```\n"
quoted_code_issue = schemas.PageIssue(
    issue_id = "quoted-code", category = "markdown_formatting", severity = "medium",
    description = "A malformed heading appears in the quoted code sample.",
    evidence = "> ##Install", confidence = 0.99,
)
quoted_code_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [quoted_code_issue]),
    markdown = quoted_code,
)
check("a fence inside a block quote protects its code from heading findings", quoted_code_assessment.issues, [])
check("the candidate scan ignores headings inside quoted fences", page_audit.domain.heading_markers(quoted_code), [])

quoted_table = "> | Name | Value |\n> | --- | --- |\n> | item | 1 |\n"
check("a quoted GFM header and alignment row are valid table syntax",
      page_audit.domain.is_sound_table_head("> | Name | Value |\n> | --- | --- |"), True)
check("a quoted body row is recognized in its source table",
      page_audit.domain.is_valid_table_row_context(quoted_table, "> | item | 1 |"), True)

figure = '<figure>![](/api/v1/dd/ingestion/slug/artifacts/chart.png)<figcaption>Chart</figcaption></figure>'
image_issue = schemas.PageIssue(
    issue_id = "image", category = "markdown_formatting", severity = "medium",
    description = "The image syntax is missing a closing parenthesis.",
    evidence = "![](/api/v1/dd/ingestion/slug/artifacts/chart.png)", confidence = 0.99,
)
image_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [image_issue]),
    markdown = figure,
)
check("a complete image inside an HTML figure is not reported as malformed", image_assessment.issues, [])
check("a cited exclamation is suppressed only when it is the figure image marker",
      page_audit.domain.is_reported_exclamation_mark_a_figure_image(
          figure, "!", "a solitary exclamation mark appears before the figure image",
      ), True)
check("an unrelated exclamation is not hidden by another figure image",
      page_audit.domain.is_reported_exclamation_mark_a_figure_image(
          figure + "\nAn unrelated ! remains.\n", "!", "a stray exclamation was reported",
      ), False)
broken_image = "![](images/chart.png\n"
broken_image_issue = image_issue.model_copy(update = {"evidence": "![](images/chart.png"})
broken_image_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [broken_image_issue]),
    markdown = broken_image,
)
check("an actually unclosed image remains available for review", [issue.issue_id for issue in broken_image_assessment.issues], ["image"])

plain_heading_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [
        schemas.PageIssue(
            issue_id = "plain-heading", category = "malformed_heading", severity = "medium",
            description = "Malformed heading because the plain text label has no ATX heading marker.",
            evidence = "Reference", confidence = 0.99,
        ),
    ]),
    markdown = "Reference\n\nThe API reference follows.\n",
)
check("plain text labels alone do not prove that Markdown heading syntax is malformed", plain_heading_assessment.issues, [])

sphinx_source = (
    "asyncio.EventLoop\n"
    "    : abstract base class for event loops\n"
    "\n"
    "        Added in version 3.13.\n"
)
sphinx_version_issue = schemas.PageIssue(
    issue_id = "sphinx-version", category = "markdown_formatting", severity = "medium",
    description = "The indentation in this API documentation is malformed.",
    evidence = "        Added in version 3.13.", confidence = 0.99,
)
sphinx_version_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [sphinx_version_issue]),
    markdown = sphinx_source,
)
check("an indented Sphinx version annotation is part of its definition list", sphinx_version_assessment.issues, [])

escaped_signature = "asyncio.eager\\_task\\_factory\n    : task factory\n"
escaped_signature_issue = schemas.PageIssue(
    issue_id = "escaped-signature", category = "encoding_artifact", severity = "medium",
    description = "Escaped underscores in this identifier are an encoding artifact.",
    evidence = "asyncio.eager\\_task\\_factory", confidence = 0.99,
)
escaped_signature_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [escaped_signature_issue]),
    markdown = escaped_signature,
)
check("valid escaped identifiers in Sphinx signatures are not encoding artifacts", escaped_signature_assessment.issues, [])

sphinx_wrapped_source = (
    "asyncio.Queue.get()\n"
    "    : Retrieve an item when one is available and wait\n"
    "      until an item has been put into the queue.\n"
)
sphinx_wrapped_issue = schemas.PageIssue(
    issue_id = "sphinx-wrapped-prose", category = "markdown_formatting", severity = "medium",
    description = "This line break in the wrapped sentence harms its rendering and readability.",
    evidence = "    : Retrieve an item when one is available and wait\n      until an item has been put into the queue.",
    confidence = 0.99,
)
sphinx_wrapped_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [sphinx_wrapped_issue]),
    markdown = sphinx_wrapped_source,
)
check("ordinary wrapped prose in Sphinx API descriptions is not a formatting defect", sphinx_wrapped_assessment.issues, [])

sphinx_asterisk_source = "class asyncio.Semaphore(*value=1*)\n    : A semaphore object.\n"
sphinx_asterisk_issue = schemas.PageIssue(
    issue_id = "sphinx-asterisk-style", category = "markdown_formatting", severity = "medium",
    description = "The unusual asterisk placement around the default parameter is malformed.",
    evidence = "class asyncio.Semaphore(*value=1*)",
    confidence = 0.99,
)
sphinx_asterisk_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [sphinx_asterisk_issue]),
    markdown = sphinx_asterisk_source,
)
check("valid Sphinx italic default-parameter signatures are not flagged for asterisk style", sphinx_asterisk_assessment.issues, [])

sphinx_repeat_source = (
    "# Future.result\n"
    "asyncio.Future.result()\n"
    "    : Raises CancelledError if the future is cancelled.\n"
    "\n"
    "# Future.exception\n"
    "asyncio.Future.exception()\n"
    "    : Raises CancelledError if the future is cancelled.\n"
)
sphinx_repeat_issue = schemas.PageIssue(
    issue_id = "sphinx-context-repeat", category = "markdown_formatting", severity = "medium",
    description = "The same cancellation explanation is repeated across these API methods.",
    evidence = "Raises CancelledError if the future is cancelled.",
    other_evidence = "Raises CancelledError if the future is cancelled.",
    confidence = 0.99,
)
sphinx_repeat_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [sphinx_repeat_issue]),
    markdown = sphinx_repeat_source,
)
check("repeated Sphinx explanation in separate API entries is contextual, not a formatting issue", sphinx_repeat_assessment.issues, [])

sphinx_same_entry_source = (
    "# Future.result\n"
    "asyncio.Future.result()\n"
    "    : Raises CancelledError if the future is cancelled.\n"
    "      Raises CancelledError if the future is cancelled.\n"
)
sphinx_same_entry_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [sphinx_repeat_issue]),
    markdown = sphinx_same_entry_source,
)
check("same-entry Sphinx repetition remains reviewable", [issue.issue_id for issue in sphinx_same_entry_assessment.issues], ["sphinx-context-repeat"])

escaped_emphasis_issue = schemas.PageIssue(
    issue_id = "missing-escaped-literal", category = "markdown_formatting", severity = "medium",
    description = "The source identifier *return\\_when* is visual noise and should be removed.",
    evidence = "The result is returned when the operation completes.", confidence = 0.99,
)
escaped_emphasis_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [escaped_emphasis_issue]),
    markdown = "The result is returned when the operation completes.\n",
)
check("an emphasized escaped identifier absent from evidence invalidates the finding", escaped_emphasis_assessment.issues, [])

sphinx_parameter_claim = schemas.PageIssue(
    issue_id = "sphinx-parameter-literal", category = "markdown_formatting", severity = "medium",
    description = "The parameter description uses backslash-escaped characters (*timeout*, *return\\_when*) that are unnecessary in this context and create visual noise.",
    evidence = "*timeout* (a float or int), if specified, can be used to control",
    confidence = 0.99,
)
sphinx_parameter_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [sphinx_parameter_claim]),
    markdown = "*timeout* (a float or int), if specified, can be used to control the wait.\n",
)
check("an emphasized escaped identifier missing from a Sphinx parameter excerpt is rejected", sphinx_parameter_assessment.issues, [])

valid_link_source = (
    "> Integrate with Browserbase using LangChain Python.\n\n"
    "[Browserbase](https://www.browserbase.com)\n\n"
    "with [session recordings](https://docs.browserbase.com/platform/browser/observability/observability) "
    "and a [live view](https://docs.browserbase.com/platform/browser/observability/session-live-view)\n\n"
    "The same API key covers [Search](https://docs.browserbase.com/platform/search/overview) and "
    "[Fetch](https://docs.browserbase.com/platform/fetch/overview).\n"
)
valid_link_issues = [
    schemas.PageIssue(
        issue_id = "broken-link-1", category = "markdown_formatting", severity = "low",
        description = "The blockquote contains broken link syntax because a closing bracket is missing.",
        evidence = "> Integrate with Browserbase using LangChain Python.\n\n[Browserbase](https://www.browserbase.com)",
        confidence = 0.99,
    ),
    schemas.PageIssue(
        issue_id = "broken-link-2", category = "markdown_formatting", severity = "low",
        description = "The text has a broken link due to a missing closing bracket.",
        evidence = "with [session recordings](https://docs.browserbase.com/platform/browser/observability/observability) and a [live view](https://docs.browserbase.com/platform/browser/observability/session-live-view)",
        confidence = 0.99,
    ),
    schemas.PageIssue(
        issue_id = "broken-link-3", category = "markdown_formatting", severity = "low",
        description = "This line has broken link syntax; a closing bracket is missing.",
        evidence = "The same API key covers [Search](https://docs.browserbase.com/platform/search/overview) and [Fetch](https://docs.browserbase.com/platform/fetch/overview)",
        confidence = 0.99,
    ),
]
valid_link_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = valid_link_issues),
    markdown = valid_link_source,
)
check("claims of missing brackets are discarded when cited inline links are complete", valid_link_assessment.issues, [])

broken_link_text = "[Browserbase(https://www.browserbase.com)"
broken_link_issue = schemas.PageIssue(
    issue_id = "actually-broken-link", category = "markdown_formatting", severity = "medium",
    description = "This link has broken syntax because its closing bracket is missing.",
    evidence = broken_link_text, confidence = 0.99,
)
broken_link_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [broken_link_issue]),
    markdown = broken_link_text,
)
check("a genuinely missing Markdown link bracket remains reviewable", [issue.issue_id for issue in broken_link_assessment.issues], ["actually-broken-link"])

callout_heading = "### Execution and state\n\n> [!TIP]\n> State is checkpointed.\n"
empty_heading_issue = schemas.PageIssue(
    issue_id = "empty-heading", category = "malformed_heading", severity = "medium",
    description = "This is an empty heading because no prose follows it.",
    evidence = "### Execution and state\n\n> [!TIP]", confidence = 0.99,
)
callout_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [empty_heading_issue]),
    markdown = callout_heading,
)
check("a valid heading followed by a callout is not an empty heading", callout_assessment.issues, [])

unsupported_literal_issue = schemas.PageIssue(
    issue_id = "unsupported-literal", category = "markdown_formatting", severity = "medium",
    description = "The source line contains the identifier ``kwargs``, which is not formatted correctly.",
    evidence = "The method accepts keyword arguments.", confidence = 0.99,
)
unsupported_literal_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.API_REFERENCE, confidence = 0.9, issues = [unsupported_literal_issue]),
    markdown = "The method accepts keyword arguments.\n",
)
check("character-level claims must quote text that appears in their evidence", unsupported_literal_assessment.issues, [])

blank_line_source = "# Title\n\n## Section\n\n| Name | Value |\n| --- | --- |\n"
blank_line_issues = schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [
    schemas.PageIssue(
        issue_id = "heading-blank", category = "malformed_heading", severity = "medium",
        description = "A blank line is missing before this heading.",
        evidence = "## Section", confidence = 0.99,
    ),
    schemas.PageIssue(
        issue_id = "table-blank", category = "markdown_formatting", severity = "medium",
        description = "A blank line is missing before this table.",
        evidence = "| Name | Value |\n| --- | --- |", confidence = 0.99,
    ),
])
blank_line_assessment, _ = page_audit.domain.sanitize_assessment(blank_line_issues, markdown = blank_line_source)
check("missing-blank-line claims contradicted by source context are dropped", blank_line_assessment.issues, [])

tabs_source = (
    "{% tabs %}\n{% tab title=\"macOS\" %}\nInstall the dependency.\n{% endtab %}\n"
    "{% tab title=\"Windows\" %}\nInstall the dependency.\n{% endtab %}\n{% endtabs %}\n"
)
tab_repeat = schemas.PageIssue(
    issue_id = "tab-repeat", category = "duplicate_section", severity = "medium",
    description = "The same instruction is duplicated.",
    evidence = "Install the dependency.", other_evidence = "Install the dependency.", confidence = 0.99,
)
tabs_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [tab_repeat]),
    markdown = tabs_source,
)
check("repeated instructions in different GitBook choice tabs are contextual", tabs_assessment.issues, [])

cosmetic_assessment, _ = page_audit.domain.sanitize_assessment(
    schemas.PageAssessment(page_kind = schemas.PageKind.DOCUMENTATION, confidence = 0.9, issues = [
        schemas.PageIssue(
            issue_id = "spacing", category = "markdown_formatting", severity = "low",
            description = "Minor punctuation inconsistency is only a cosmetic style issue.",
            evidence = "A sentence.", confidence = 0.99,
        ),
    ]),
    markdown = "A sentence.\n",
)
check("low-severity cosmetic punctuation is not actionable ingestion quality", cosmetic_assessment.issues, [])
rendered = auditor_domain.system_prompt()
check("the auditor prompt carries every example and no placeholder",
      (auditor_params.CONVENTIONS_MARKER in rendered, all(example in rendered for _, examples in auditor_patterns.EXPORT_CONVENTIONS for example in examples)),
      (False, True))

REPAIR_PAGE = "# T\n\nA line.\n\nSee the guide.\n\nA line.\n"
repair_assessment = {"issues": [{"issue_id": "d", "category": "markdown_formatting", "severity": "low", "description": "redundant blank line between prose paragraphs",
                                  "evidence": "A line.", "confidence": 0.95}]}
repair_sha = hashlib.sha256(REPAIR_PAGE.encode("utf-8")).hexdigest()


def patch_fields(**changes):
    fields = {"issue_id": "d", "evidence": "A line.", "target_text": "A line.\n\nSee the guide.", "replacement": "A line.\nSee the guide.",
              "confidence": 0.95, "context_before": "\n\n", "context_after": "\n\nA line.\n"}
    return {**fields, **changes}


check("a patch the final gate would apply passes the check",
      page_repair.domain.check_patch(REPAIR_PAGE, repair_sha, repair_assessment, patch_fields()), "")
non_unique = page_repair.domain.check_patch(REPAIR_PAGE, repair_sha, repair_assessment, patch_fields(target_text = "A line."))
check("a target that repeats is refused with the count and what to do",
      ("occurs 2 times" in non_unique, "Extend target_text" in non_unique), (True, True))
check("a duplicate finding cannot be patched, even when its evidence occurs twice",
      page_repair.domain.check_patch(
          REPAIR_PAGE, repair_sha,
          {"issues": [{"issue_id": "d", "category": "duplicate_section", "evidence": "A line.", "confidence": 0.95}]},
          patch_fields(),
      ).startswith("finding category is not eligible"),
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

# a patch that takes the quote markers off part of a block quote splits a callout or a tab (the DeepAgents run's page 19)
def cuts(markdown: str, target: str, replacement: str) -> bool:
    start = markdown.index(target)
    return page_repair.domain.cuts_block_quote(markdown, start, start + len(target), replacement)


check("quote depth counts nested markers", [page_repair.domain.quote_depth(line) for line in ("plain", "> a", ">a", "> > a", "   > a", "    > a")], [0, 1, 1, 2, 1, 0])
TAB_PAGE = "Intro.\n\n> [tab] StateBackend\n>\n> A thread-scoped backend.\n>\n> ```python\n> code\n> ```\n\nAfter.\n"
check("unquoting the first lines of a quote that goes on is a cut", cuts(TAB_PAGE, "> [tab] StateBackend\n>\n> A thread-scoped backend.", "[tab] StateBackend\n\nA thread-scoped backend."), True)
check("…and so is unquoting one line in the middle of it", cuts("> A\n> B\n> C\n", "> B", "B"), True)
check("a nested callout cut from its inner lines is a cut", cuts("> > [!WARNING]\n> >\n> > Grants access.\n> > Use with care.\n", "> > [!WARNING]\n> >\n> > Grants access.", "[!WARNING]\n\nGrants access."), True)
check("unquoting a whole quote is not a cut", cuts("Intro.\n\n> A\n> B\n\nAfter.\n", "> A\n> B", "A\nB"), False)
check("the banner at the top of a page, unquoted whole, is not a cut", cuts("> ## Documentation Index\n> Fetch it.\n\n# Title\n", "> ## Documentation Index\n> Fetch it.", "## Documentation Index\nFetch it."), False)
check("a change inside a quote that keeps its markers is not a cut", cuts("> A\n> B\n> C\n", "> B", "> B2"), False)
check("a change outside any quote is not a cut", cuts("One.\n\nTwo.\n", "Two.", "Three."), False)
quote_page = "# T\n\n> [tab] StateBackend\n>\n> A thread-scoped backend.\n>\n> More.\n"
quote_assessment = {"issues": [{"issue_id": "q", "category": "markdown_formatting", "severity": "medium", "description": "d",
                                 "evidence": "> [tab] StateBackend\n>\n> A thread-scoped backend.", "confidence": 0.95}]}
quote_sha = hashlib.sha256(quote_page.encode("utf-8")).hexdigest()
quote_fields = {"issue_id": "q", "evidence": "> [tab] StateBackend\n>\n> A thread-scoped backend.", "confidence": 0.95,
                "target_text": "> [tab] StateBackend\n>\n> A thread-scoped backend.", "replacement": "[tab] StateBackend\n\nA thread-scoped backend.",
                "context_before": "# T\n\n", "context_after": "\n>\n> More.\n"}
quote_check = page_repair.domain.check_patch(quote_page, quote_sha, quote_assessment, quote_fields)
check("the check refuses it, and says what to do", (quote_check.startswith("patch takes the quote markers off part of a block quote"), "Drop the patch" in quote_check), (True, True))
_, quote_gate = page_repair.domain.apply_patches(
    quote_page, quote_sha, quote_assessment,
    schemas.PagePatchBatch(patches = [schemas.PagePatch(source_sha256 = quote_sha, rationale = "r", **quote_fields)]),
)
check("the final gate refuses it too", (len(quote_gate["accepted"]), [item["reason"] for item in quote_gate["rejected"]]),
      (0, ["patch takes the quote markers off part of a block quote"]))
