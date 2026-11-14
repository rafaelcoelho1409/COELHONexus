"""Offline checks for DD's replay-only failure injection and expected-outage grading.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_ingestion_evaluation.py
"""
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DD_STUDY_SEM", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains

evaluation_domain = importlib.import_module("domains.dd.ingestion.evaluation.domain")
evaluation_service = importlib.import_module("domains.dd.ingestion.evaluation.service")


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class FakeAuditor:
    async def ainvoke(self, request, config=None):
        return {"delegated": json.loads(request["messages"][0]["content"])["page_index"], "config": config}


async def test_replay_fault_injection() -> None:
    auditor = evaluation_service._ReplayFaultInjectingAuditor(FakeAuditor(), {65})
    request = {"messages": [{"content": json.dumps({"page_index": 65})}]}
    try:
        await auditor.ainvoke(request)
    except RuntimeError as error:
        check("the configured page gets a replay-only auditor failure", str(error),
              "Replay-injected auditor provider failure for fixture page 65")
    else:
        raise AssertionError("the configured fixture page did not trigger its injected failure")

    delegated = await auditor.ainvoke(
        {"messages": [{"content": json.dumps({"page_index": 30})}]},
        config={"trace": "preserved"},
    )
    check("non-target pages still call the real auditor", delegated,
          {"delegated": 30, "config": {"trace": "preserved"}})


def test_expected_outage_grading() -> None:
    fixture = evaluation_domain.fixtures()["deepagents-page-65-structured-audit-outage"]
    source = "# Frozen source\n\nKeep these bytes.\n"
    unavailable_record = {
        "audit_status": "unavailable",
        "assessment": {"issues": []},
        "patches": {"accepted": [], "rejected": []},
    }
    result = evaluation_domain.evaluate_page(fixture, {}, source, source, source, unavailable_record)
    checks = {row["name"]: row["passed"] for row in result["checks"]}
    check("injected outage status is required", checks["expected_audit_status"], True)
    check("unavailable audit preserves normalized source", checks["unavailable_stage_preserves_normalized_source"], True)
    check("expected outage has no failed deterministic checks", result["failed_checks"], [])
    check("expected outage remains labeled for human review", result["disposition"], "needs_human_review")

    completed_record = {**unavailable_record, "audit_status": "completed"}
    mismatch = evaluation_domain.evaluate_page(fixture, {}, source, source, source, completed_record)
    status_check = next(row for row in mismatch["checks"] if row["name"] == "expected_audit_status")
    check("fixture fails if outage injection is missing", status_check["passed"], False)
    check("missing outage is a deterministic fixture failure", mismatch["disposition"], "failed")


def test_r3_false_positive_fixtures() -> None:
    round_id = "dd-refine-2026-11-14-r3"
    deepagents_run = "3324037265224d729cc08baf0355a013"
    asyncio_run = "84a14914693e4d24bc94034e23aac1fa"
    deepagents = evaluation_domain.select_fixtures(round_id, deepagents_run, [80])
    asyncio_pages = evaluation_domain.select_fixtures(round_id, asyncio_run, [0, 4, 42, 54, 99])
    check("the DeepAgents valid-link regression is pinned to page 80 of the r3 full run",
          [(fixture["id"], fixture["page_idx"]) for fixture in deepagents],
          [("deepagents-page-80-valid-inline-links", 80)])
    check("all five asyncio Sphinx regressions are pinned to the r3 full run",
          [fixture["page_idx"] for fixture in asyncio_pages], [0, 4, 42, 54, 99])

    fixture = deepagents[0]
    source = "[Browserbase](https://www.browserbase.com)\n"
    stale_finding = {
        "issue_id": "false-link", "category": "markdown_formatting",
        "evidence": "[Browserbase](https://www.browserbase.com)",
    }
    report = evaluation_domain.evaluate_page(
        fixture, {}, source, source, source,
        {"audit_status": "completed", "assessment": {"issues": [stale_finding]},
         "patches": {"accepted": [], "rejected": []}},
    )
    forbidden_check = next(row for row in report["checks"] if row["name"] == "forbidden_audit_evidence")
    check("the page-80 fixture fails if the valid link returns as audit evidence", forbidden_check["passed"], False)


async def main() -> None:
    await test_replay_fault_injection()
    test_expected_outage_grading()
    test_r3_false_positive_fixtures()


asyncio.run(main())
