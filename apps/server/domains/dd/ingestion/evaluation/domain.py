"""Pure fixture, provenance and deterministic grading rules for DD page replay."""
from __future__ import annotations

import ai
import domains
import infra

import hashlib
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


FIXTURE_PATH = Path(__file__).with_name("fixtures.json")


def fixtures() -> dict[str, dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = payload.get("fixtures") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("DD ingestion evaluation fixtures must contain a fixtures list")
    indexed = {
        row["id"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if len(indexed) != len(rows):
        raise ValueError("DD ingestion evaluation fixtures contain invalid or duplicate ids")
    return indexed


def select_fixtures(round_id: str, run_id: str, page_indices: list[int] | None = None) -> list[dict[str, Any]]:
    rows = [
        row for row in fixtures().values()
        if row.get("round_id") == round_id and row.get("run_id") == run_id
    ]
    if page_indices is not None:
        by_idx = {row["page_idx"]: row for row in rows}
        missing = sorted(set(page_indices) - set(by_idx))
        if missing:
            raise ValueError(f"no curated fixtures for page indices {missing} in source run {run_id}")
        rows = [by_idx[index] for index in page_indices]
    return sorted(rows, key=lambda row: row["page_idx"])


def provenance() -> dict[str, Any]:
    """Build safe, reproducible metadata; secrets and full endpoint URLs are intentionally excluded."""
    auditor_prompt = ai.dd.agents.ingestion.page_auditor.domain.system_prompt()
    repair_prompt = ai.dd.prompts.ingestion.page_repair.prompts.SYSTEM_PROMPT
    prompt_hashes = {
        "page_auditor": hashlib.sha256(auditor_prompt.encode("utf-8")).hexdigest(),
        "page_repair": hashlib.sha256(repair_prompt.encode("utf-8")).hexdigest(),
    }
    prompt_hashes["combined"] = hashlib.sha256(
        json.dumps(prompt_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    auditor_config = ai.dd.agents.ingestion.page_auditor.config.MODEL
    repair_config = ai.dd.agents.ingestion.page_repair.config.MODEL
    model_config = {
        "provider": "openai-compatible",
        "model": str(domains.settings.chat.service.ENDPOINT.model),
        "page_auditor": asdict(auditor_config) if is_dataclass(auditor_config) else {},
        "page_repair": asdict(repair_config) if is_dataclass(repair_config) else {},
        "page_auditor_max_calls": ai.dd.agents.ingestion.page_auditor.params.MODEL_CALLS,
        "page_repair_max_calls": ai.dd.agents.ingestion.page_repair.params.MODEL_CALLS,
        "audit_attempts": ai.dd.nodes.ingestion.page_audit.params.MAX_ATTEMPTS,
        "repair_attempts": ai.dd.nodes.ingestion.page_repair.params.MAX_ATTEMPTS,
        "max_page_chars": ai.dd.nodes.ingestion.page_audit.params.MAX_PAGE_MARKDOWN_CHARS,
    }
    app_root = Path(__file__).resolve().parents[4]
    source_files = {
        "extraction_and_preparation": tuple(
            path.relative_to(app_root).as_posix()
            for root in (
                app_root / "domains/dd/ingestion/tiers/tier1",
                app_root / "domains/dd/ingestion/tiers/tier2",
                app_root / "domains/dd/ingestion/tiers/tier3",
                app_root / "domains/dd/ingestion/tiers/tier4",
                app_root / "domains/dd/ingestion/post",
                app_root / "domains/dd/ingestion/capture",
                app_root / "domains/dd/ingestion/storage",
            )
            for path in sorted(root.rglob("*.py"))
        ) + (
            "domains/dd/ingestion/runtime/dispatch/service.py",
            "domains/dd/ingestion/runtime/dispatch/domain.py",
            "domains/dd/ingestion/runtime/dispatch/params.py",
        ),
        "normalizer": (
            "domains/dd/ingestion/ai/service.py",
            "domains/dd/ingestion/corpus_normalize/domain.py",
            "domains/dd/ingestion/corpus_normalize/patterns.py",
            "domains/dd/ingestion/corpus_normalize/schemas.py",
            "domains/dd/ingestion/corpus_normalize/versions.py",
        ),
        "auditor": (
            "ai/dd/agents/schemas.py",
            "ai/dd/agents/service.py",
            "ai/dd/agents/ingestion/page_auditor/agent.py",
            "ai/dd/agents/ingestion/page_auditor/config.py",
            "ai/dd/agents/ingestion/page_auditor/domain.py",
            "ai/dd/agents/ingestion/page_auditor/params.py",
            "ai/dd/nodes/ingestion/page_audit/domain.py",
            "ai/dd/nodes/ingestion/page_audit/node.py",
            "ai/dd/nodes/ingestion/page_audit/params.py",
            "ai/dd/nodes/ingestion/page_audit/patterns.py",
            "ai/dd/nodes/ingestion/audit_batch/service.py",
        ),
        "repairer": (
            "ai/dd/agents/schemas.py",
            "ai/dd/agents/service.py",
            "ai/dd/agents/ingestion/page_repair/agent.py",
            "ai/dd/agents/ingestion/page_repair/config.py",
            "ai/dd/agents/ingestion/page_repair/params.py",
            "ai/dd/nodes/ingestion/page_repair/domain.py",
            "ai/dd/nodes/ingestion/page_repair/params.py",
            "ai/dd/nodes/ingestion/page_repair/node.py",
            "ai/dd/tools/ingestion/check_patch/params.py",
            "ai/dd/tools/ingestion/check_patch/schemas.py",
            "ai/dd/tools/ingestion/check_patch/tool.py",
            "ai/dd/tools/ingestion/check_patch/domain.py",
            "ai/dd/tools/ingestion/check_patch/service.py",
            "ai/dd/nodes/ingestion/normalize_pages/service.py",
        ),
        "evaluator": (
            "domains/dd/ingestion/evaluation/domain.py",
            "domains/dd/ingestion/evaluation/service.py",
            "domains/dd/ingestion/evaluation/fixtures.json",
        ),
    }
    code_hashes = {}
    for component, paths in source_files.items():
        files = {
            path: hashlib.sha256((app_root / path).read_bytes()).hexdigest()
            for path in paths
        }
        code_hashes[component] = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    build = {
        "service_version": infra.opentelemetry.params.SERVICE_VERSION,
        "git_sha": infra.opentelemetry.params.GIT_SHA,
        "corpus_normalizer_version": domains.dd.ingestion.corpus_normalize.versions.NORMALIZER_VERSION,
        "agentic_normalizer_version": domains.dd.ingestion.ai.params.NORMALIZER_VERSION,
        "pipeline_code_sha256": code_hashes,
        "prompt_sha256": prompt_hashes,
        "agent_config": model_config,
    }
    fingerprint_payload = json.dumps(build, sort_keys=True, separators=(",", ":"))
    build["fingerprint"] = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
    return build


def evaluate_page(
    fixture: dict[str, Any],
    normalization_stats: dict[str, int],
    source_body: str,
    normalized_body: str,
    output_body: str,
    audit_record: dict[str, Any],
) -> dict[str, Any]:
    """Apply objective fixture assertions and leave nuanced decisions for the recorded human rubric."""
    checks: list[dict[str, Any]] = []

    second_pass = domains.dd.ingestion.corpus_normalize.domain.normalize_doc(normalized_body)
    checks.append({
        "name": "normalizer_idempotence",
        "passed": second_pass.body == normalized_body,
        "detail": "second normalization must produce identical Markdown",
    })

    expected = fixture.get("expected") or {}
    expected_audit_status = expected.get("audit_status")
    audit_status = audit_record.get("audit_status") or "unknown"
    if expected_audit_status is not None:
        checks.append({
            "name": "expected_audit_status",
            "passed": audit_status == expected_audit_status,
            "expected": expected_audit_status,
            "actual": audit_status,
        })
    for name, constraint in (expected.get("normalizer_stats") or {}).items():
        actual = int(normalization_stats.get(name, 0) or 0)
        minimum = int((constraint or {}).get("gte", 0))
        checks.append({
            "name": f"normalizer_stat:{name}",
            "passed": actual >= minimum,
            "actual": actual,
            "expected_gte": minimum,
        })

    assessment = audit_record.get("assessment") or {}
    issues = assessment.get("issues") if isinstance(assessment, dict) else []
    issues = issues if isinstance(issues, list) else []
    evidence = [
        value
        for issue in issues if isinstance(issue, dict)
        for value in (issue.get("evidence"), issue.get("other_evidence"))
        if isinstance(value, str)
    ]
    for forbidden in expected.get("forbidden_evidence", []):
        found = any(forbidden in value for value in evidence)
        checks.append({
            "name": "forbidden_audit_evidence",
            "passed": not found,
            "evidence": forbidden,
        })

    descriptions = [
        issue.get("description")
        for issue in issues
        if isinstance(issue, dict) and isinstance(issue.get("description"), str)
    ]
    for forbidden in expected.get("forbidden_descriptions", []):
        found = any(forbidden.casefold() in description.casefold() for description in descriptions)
        checks.append({
            "name": "forbidden_audit_description",
            "passed": not found,
            "description": forbidden,
        })

    expected_categories = {
        category
        for category in expected.get("expected_issue_categories", [])
        if isinstance(category, str) and category
    }
    actual_categories = {
        issue.get("category")
        for issue in issues
        if isinstance(issue, dict) and isinstance(issue.get("category"), str)
    }
    missing_categories = sorted(expected_categories - actual_categories)
    if expected_categories:
        checks.append({
            "name": "expected_issue_categories",
            "passed": not missing_categories,
            "expected": sorted(expected_categories),
            "actual": sorted(actual_categories),
            "missing": missing_categories,
        })

    expected_repair_status = expected.get("repair_status")
    if expected_repair_status is not None:
        repair = audit_record.get("repair") or {}
        actual_repair_status = repair.get("status") if isinstance(repair, dict) else None
        checks.append({
            "name": "expected_repair_status",
            "passed": actual_repair_status == expected_repair_status,
            "expected": expected_repair_status,
            "actual": actual_repair_status,
        })

    patches = audit_record.get("patches") or {}
    accepted = patches.get("accepted") if isinstance(patches, dict) else []
    accepted_count = len(accepted) if isinstance(accepted, list) else 0
    max_accepted = expected.get("max_accepted_repairs")
    if max_accepted is not None:
        checks.append({
            "name": "repair_safety_budget",
            "passed": accepted_count <= int(max_accepted),
            "accepted_repairs": accepted_count,
            "maximum": int(max_accepted),
        })

    checks.append({
        "name": "source_snapshot_recorded",
        "passed": bool(source_body),
        "source_chars": len(source_body),
        "normalized_chars": len(normalized_body),
        "source_sha256": hashlib.sha256(source_body.encode("utf-8")).hexdigest(),
        "normalized_sha256": hashlib.sha256(normalized_body.encode("utf-8")).hexdigest(),
    })
    repair_status = (audit_record.get("repair") or {}).get("status")
    if audit_status == "unavailable" or repair_status == "unavailable":
        checks.append({
            "name": "unavailable_stage_preserves_normalized_source",
            "passed": output_body == normalized_body,
            "detail": "an unavailable audit or repair must retain the normalized source byte-for-byte",
        })
    failures = [check for check in checks if not check["passed"]]
    if failures:
        disposition = "failed"
    elif audit_status != "completed" and audit_status != expected_audit_status:
        disposition = "inconclusive"
    else:
        disposition = "needs_human_review"
    return {
        "fixture_id": fixture["id"],
        "labels": fixture.get("labels", []),
        "checks": checks,
        "failed_checks": failures,
        "audit_status": audit_status,
        "human_review_required": bool(fixture.get("human_rubric")) or audit_status != "completed",
        "human_rubric": fixture.get("human_rubric", ""),
        "disposition": disposition,
    }
