"""Offline tests for the BD Page Auditor evaluation scorer.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_page_auditor_scoring.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "evaluations" / "bd" / "page_auditor"))

from scoring import score_results


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


book_id = "book-1"
extraction_run_id = "run-1"
gold_records = [
    {
        "book_id": book_id,
        "extraction_run_id": extraction_run_id,
        "page_number": 2,
        "source_sha256": "a" * 64,
        "page_kind": "blank",
        "issue_codes": [],
    },
    {
        "book_id": book_id,
        "extraction_run_id": extraction_run_id,
        "page_number": 3,
        "source_sha256": "b" * 64,
        "page_kind": "text",
        "issue_codes": ["control_characters"],
    },
    {
        "book_id": book_id,
        "extraction_run_id": extraction_run_id,
        "page_number": 5,
        "source_sha256": "c" * 64,
        "page_kind": "text",
        "issue_codes": ["split_word"],
    },
]
results_report = {
    "book_id": book_id,
    "extraction_run_id": extraction_run_id,
    "results": [
        {
            "page_number": 2,
            "source_sha256": "a" * 64,
            "assessment": {"page_kind": "blank", "issues": []},
        },
        {
            "page_number": 3,
            "source_sha256": "b" * 64,
            "assessment": {
                "page_kind": "text",
                "issues": [{"code": "control_characters"}],
            },
        },
        {"page_number": 5, "error": {"type": "ValueError", "message": "audit failed"}},
    ],
}

score = score_results(gold_records, results_report)
check("gold-page coverage", score["covered_pages"], 2)
check("missing pages are reported", score["missing_pages"], [5])
check("page-kind accuracy uses completed pages", score["page_kind_accuracy"], 1.0)
check("issue true positives", score["issue_metrics"]["true_positives"], 1)
check("missing-page issues count as false negatives", score["issue_metrics"]["false_negatives"], 1)
check("issue precision", score["issue_metrics"]["precision"], 1.0)
check("issue recall", score["issue_metrics"]["recall"], 0.5)

stale_results = {
    **results_report,
    "results": [
        {**results_report["results"][0], "source_sha256": "d" * 64},
        *results_report["results"][1:],
    ],
}
rejects("source-hash mismatches cannot be scored", lambda: score_results(gold_records, stale_results))
