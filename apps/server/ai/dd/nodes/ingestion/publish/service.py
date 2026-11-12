"""Validate graph completeness and publish the immutable run manifest last."""

from __future__ import annotations
import domains
from .... import graphs

from collections.abc import Awaitable, Callable
from typing import Any
import json


ArtifactWriter = Callable[[str, str, str], Awaitable[None]]


def _order_by_source_pages(
    items: list[dict[str, Any]],
    expected_indices: list[int],
    label: str,
) -> list[dict[str, Any]]:
    """Require exact one-to-one page coverage, then restore source manifest order."""
    if len(set(expected_indices)) != len(expected_indices):
        raise ValueError("source pages contain duplicate page indices")

    by_idx: dict[int, dict[str, Any]] = {}
    for item in items:
        idx = item.get("idx")
        if idx in by_idx:
            raise ValueError(f"{label} contain duplicate page index {idx}")
        by_idx[idx] = item

    expected = set(expected_indices)
    actual = set(by_idx)
    missing = [idx for idx in expected_indices if idx not in actual]
    extra = sorted(actual - expected, key=str)
    if missing or extra:
        raise ValueError(f"{label} do not cover source pages; missing={missing}, extra={extra}")
    return [by_idx[idx] for idx in expected_indices]


async def publish(
    state: graphs.ingestion.states.IngestionState,
    write_artifact: ArtifactWriter,
) -> dict[str, Any]:
    """Write an agentic manifest only after every source page has an output."""
    pages = state.get("pages", [])
    assessments = state.get("assessments", [])
    outputs = state.get("output_pages", [])
    expected_indices = [page["idx"] for page in pages]
    assessments = _order_by_source_pages(assessments, expected_indices, "page assessments")
    outputs = _order_by_source_pages(outputs, expected_indices, "normalized pages")
    if any(
        not page.get("key") or not page.get("source_key") or not page.get("raw_key")
        for page in outputs
    ):
        raise ValueError(
            "each published page must reference its agentic, source, and raw objects"
        )

    framework_slug = state.get("framework_slug")
    run_id = state.get("run_id")
    if not isinstance(framework_slug, str) or not isinstance(run_id, str):
        raise ValueError("the ingestion graph state has no framework or run identity")

    payload = {
        "framework_slug": framework_slug,
        "run_id": run_id,
        "normalizer": domains.dd.ingestion.ai.params.NORMALIZER_VERSION,
        "page_count": len(outputs),
        "source_page_count": len(pages),
        "pages": outputs,
        "assessments": [
            domains.dd.ingestion.storage.keys.agentic_assessment_key(
                framework_slug,
                run_id,
                item["idx"],
            )
            for item in assessments
        ],
        "audits": [
            domains.dd.ingestion.storage.keys.agentic_audit_key(
                framework_slug,
                run_id,
                item["idx"],
            )
            for item in assessments
        ],
    }
    manifest_key = domains.dd.ingestion.storage.keys.agentic_manifest_key(
        framework_slug, run_id
    )
    await write_artifact(
        manifest_key,
        json.dumps(payload, ensure_ascii = False),
        domains.dd.ingestion.ai.params.JSON_TYPE,
    )
    return {
        "summary": {
            "manifest_key": manifest_key,
            "page_count": payload["page_count"],
            "audit_unavailable_pages": [
                item["idx"] for item in assessments if item.get("audit_status") == "unavailable"
            ],
            "repair_unavailable_pages": [
                page["idx"] for page in outputs if page.get("repair_status") == "unavailable"
            ],
        }
    }
