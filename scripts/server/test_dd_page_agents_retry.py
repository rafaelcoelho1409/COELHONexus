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

check("both page agents use a bounded 120-second request with SDK retries disabled",
      (auditor_config.MODEL.timeout_s, repair_config.MODEL.timeout_s,
       auditor_config.MODEL.max_retries, repair_config.MODEL.max_retries),
      (120.0, 120.0, 0, 0))


async def audit(agent):
    return await page_audit.node.audit_page("locust", page, markdown, agent, files = {})


async def repair(agent):
    return await page_repair.node.repair_page(
        "locust", page, markdown, "0" * 64, {"issues": []}, agent, files = {},
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
            {},
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
check("it is asked as many times as allowed, no more", never.calls, attempts)
