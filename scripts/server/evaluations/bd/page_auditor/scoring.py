"""Score live Page Auditor results against fully reviewed gold labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding = "utf-8").splitlines(), start = 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: each gold record must be a JSON object")
        records.append(record)
    if not records:
        raise ValueError(f"{path}: no gold records were found")
    return records


def _required_text(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: {key} must be a non-empty string")
    return value


def _page_number(record: dict[str, Any], context: str) -> int:
    value = record.get("page_number")
    if type(value) is not int or value <= 0:
        raise ValueError(f"{context}: page_number must be a positive integer")
    return value


def _issue_codes(value: Any, context: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(code, str) or not code for code in value):
        raise ValueError(f"{context}: issue_codes must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{context}: issue_codes must not contain duplicates")
    return set(value)


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_results(
    gold_records: list[dict[str, Any]],
    results_report: dict[str, Any],
) -> dict[str, Any]:
    """Compare source-pinned, exhaustive page labels with one runner report."""
    if not gold_records:
        raise ValueError("at least one gold record is required")
    if not isinstance(results_report, dict) or not isinstance(results_report.get("results"), list):
        raise ValueError("results report must contain a results list")

    report_book_id = _required_text(results_report, "book_id", "results report")
    report_run_id = _required_text(results_report, "extraction_run_id", "results report")
    gold_by_page: dict[int, dict[str, Any]] = {}
    for index, record in enumerate(gold_records, start = 1):
        context = f"gold record {index}"
        book_id = _required_text(record, "book_id", context)
        run_id = _required_text(record, "extraction_run_id", context)
        page_number = _page_number(record, context)
        source_sha256 = _required_text(record, "source_sha256", context)
        page_kind = _required_text(record, "page_kind", context)
        issue_codes = _issue_codes(record.get("issue_codes"), context)
        if book_id != report_book_id or run_id != report_run_id:
            raise ValueError(f"{context}: book/run does not match the results report")
        if page_number in gold_by_page:
            raise ValueError(f"{context}: duplicate gold label for page {page_number}")
        gold_by_page[page_number] = {
            "source_sha256": source_sha256,
            "page_kind": page_kind,
            "issue_codes": issue_codes,
        }

    result_by_page: dict[int, dict[str, Any]] = {}
    for index, record in enumerate(results_report["results"], start = 1):
        context = f"result {index}"
        if not isinstance(record, dict):
            raise ValueError(f"{context}: each result must be an object")
        page_number = _page_number(record, context)
        if page_number in result_by_page:
            raise ValueError(f"{context}: duplicate result for page {page_number}")
        result_by_page[page_number] = record

    matched_pages = []
    missing_pages = []
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    correct_page_kinds = 0

    for page_number, gold in gold_by_page.items():
        result = result_by_page.get(page_number)
        assessment = result.get("assessment") if result else None
        if not isinstance(assessment, dict):
            missing_pages.append(page_number)
            false_negatives += len(gold["issue_codes"])
            continue

        result_hash = _required_text(result, "source_sha256", f"page {page_number} result")
        if result_hash != gold["source_sha256"]:
            raise ValueError(f"page {page_number}: source hash does not match its gold label")

        predicted_kind = _required_text(assessment, "page_kind", f"page {page_number} assessment")
        correct_page_kinds += predicted_kind == gold["page_kind"]
        issues = assessment.get("issues")
        if not isinstance(issues, list):
            raise ValueError(f"page {page_number}: assessment issues must be a list")
        predicted_codes = set()
        for issue in issues:
            if not isinstance(issue, dict):
                raise ValueError(f"page {page_number}: each issue must be an object")
            predicted_codes.add(_required_text(issue, "code", f"page {page_number} issue"))

        true_positives += len(predicted_codes & gold["issue_codes"])
        false_positives += len(predicted_codes - gold["issue_codes"])
        false_negatives += len(gold["issue_codes"] - predicted_codes)
        matched_pages.append(page_number)

    predicted_issue_count = true_positives + false_positives
    gold_issue_count = true_positives + false_negatives
    precision = _fraction(true_positives, predicted_issue_count)
    recall = _fraction(true_positives, gold_issue_count)
    f1 = (
        2 * true_positives / (2 * true_positives + false_positives + false_negatives)
        if 2 * true_positives + false_positives + false_negatives
        else None
    )

    return {
        "book_id": report_book_id,
        "extraction_run_id": report_run_id,
        "gold_pages": len(gold_by_page),
        "covered_pages": len(matched_pages),
        "missing_pages": sorted(missing_pages),
        "unlabeled_result_pages": sorted(set(result_by_page) - set(gold_by_page)),
        "page_kind_accuracy": _fraction(correct_page_kinds, len(matched_pages)),
        "issue_metrics": {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score page-auditor output against reviewed JSONL gold labels.")
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    arguments = parser.parse_args()

    try:
        gold_records = _read_jsonl(arguments.gold)
        results_report = json.loads(arguments.results.read_text(encoding = "utf-8"))
        report = score_results(gold_records, results_report)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    print(json.dumps(report, ensure_ascii = False, indent = 2))
    return int(bool(report["missing_pages"]))


if __name__ == "__main__":
    raise SystemExit(main())
