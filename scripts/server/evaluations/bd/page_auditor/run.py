#!/usr/bin/env python3
"""Run selected pages through the live BD Page Auditor without changing book data.

    cd apps/server && .venv/bin/python \
        ../../scripts/server/evaluations/bd/page_auditor/run.py \
        --book-id BOOK_ID --extraction-id RUN_ID --pages 2 3 5 \
        --confirm-live-model-calls
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[5] / "apps" / "server"
sys.path.insert(0, str(SERVER_ROOT))

import ai, infra
from domains.bd.ingestion.ai.service import run_page_audit


def _safe_segment(value: str) -> bool:
    return bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


def _positive_page(value: str) -> int:
    try:
        page_number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("page numbers must be integers") from error
    if page_number <= 0:
        raise argparse.ArgumentTypeError("page numbers must be positive")
    return page_number


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run selected pages through the live BD Page Auditor without changing book data.",
    )
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--extraction-id", required=True, help="Pin the exact completed extraction run to evaluate.")
    parser.add_argument("--pages", required=True, nargs="+", type=_positive_page)
    parser.add_argument(
        "--confirm-live-model-calls",
        action="store_true",
        help="Confirm that each selected page will be sent to the configured COELHO LLM Rotator.",
    )
    arguments = parser.parse_args()

    if not _safe_segment(arguments.book_id):
        parser.error("book ID must be a safe path segment")
    if not _safe_segment(arguments.extraction_id):
        parser.error("extraction ID must be a safe path segment")
    if len(arguments.pages) != len(set(arguments.pages)):
        parser.error("page numbers must not contain duplicates")
    if not arguments.confirm_live_model_calls:
        parser.error("pass --confirm-live-model-calls to run paid live model evaluations")
    return arguments


async def _evaluate(arguments: argparse.Namespace) -> int:
    storage = infra.minio.service.get_storage()
    auditor = ai.bd.agents.ingestion.page_auditor.agent.page_auditor()
    finding_verifier = ai.bd.agents.ingestion.finding_verifier.agent.finding_verifier()
    results = []

    for page_number in arguments.pages:
        try:
            result = await run_page_audit(
                arguments.book_id,
                page_number,
                expected_extraction_id = arguments.extraction_id,
                storage = storage,
                auditor = auditor,
                finding_verifier = finding_verifier,
            )
            results.append({
                "page_number": page_number,
                "source_sha256": result["source_sha256"],
                "audit_mode": result["audit_mode"],
                "warnings": result["warnings"],
                "assessment": result["assessment"],
            })
        except Exception as error:
            results.append({
                "page_number": page_number,
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            })

    completed = [result for result in results if "assessment" in result]
    issue_codes = Counter(
        issue["code"]
        for result in completed
        for issue in result["assessment"]["issues"]
    )
    page_kinds = Counter(result["assessment"]["page_kind"] for result in completed)
    audit_modes = Counter(result["audit_mode"] for result in completed)
    report = {
        "book_id": arguments.book_id,
        "extraction_run_id": arguments.extraction_id,
        "pages_requested": arguments.pages,
        "summary": {
            "completed": len(completed),
            "failed": len(results) - len(completed),
            "page_kinds": dict(sorted(page_kinds.items())),
            "issue_codes": dict(sorted(issue_codes.items())),
            "audit_modes": dict(sorted(audit_modes.items())),
        },
        "results": results,
    }
    print(json.dumps(report, ensure_ascii = False, indent = 2))
    # A page the model could not answer falls back to the code's findings and still "completes"; it is not a scored audit.
    return int(len(completed) != len(results) or audit_modes["deterministic_fallback"] > 0)


def main() -> int:
    arguments = _arguments()
    try:
        return asyncio.run(_evaluate(arguments))
    except Exception as error:
        print(f"Page Auditor evaluation could not start: {type(error).__name__}: {error}", file = sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
