"""Validate graph completeness and publish the immutable run manifest last."""

from __future__ import annotations
import domains
from .... import graphs

from collections.abc import Awaitable, Callable
import json
from typing import Any


ArtifactWriter = Callable[[str, str, str], Awaitable[None]]


async def publish(
    state: graphs.ingestion.state.IngestionState,
    write_artifact: ArtifactWriter,
) -> dict[str, Any]:
    """Write an agentic manifest only after every source page has an output."""
    pages = state.get("pages", [])
    assessments = state.get("assessments", [])
    outputs = state.get("output_pages", [])
    expected_indices = [page["idx"] for page in pages]
    if len(assessments) != len(pages):
        raise ValueError(
            "cannot publish an ingestion graph with incomplete page audits"
        )
    if [item["idx"] for item in assessments] != expected_indices:
        raise ValueError("page assessments must cover every source page in order")
    if len(outputs) != len(pages):
        raise ValueError("cannot publish an incomplete normalized page set")
    if [page["idx"] for page in outputs] != expected_indices:
        raise ValueError("normalized pages must cover every source page in order")
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
        json.dumps(payload, ensure_ascii=False),
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
