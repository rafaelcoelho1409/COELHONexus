"""Read-only coordination for bounded full-book page audits."""
from __future__ import annotations
import domains
from .... import agents, graphs
from .. import inventory, page_audit

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


PageReader = Callable[[str], str | None | Awaitable[str | None]]
logger = logging.getLogger(__name__)


class Auditor(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def _report(progress_callback, **event: Any) -> None:
    if progress_callback is None:
        return
    result = progress_callback(event)
    if inspect.isawaitable(result):
        await result


async def audit_pages(
    state: graphs.ingestion.state.IngestionRunState,
    read_page: PageReader,
    auditor: Auditor,
    progress_callback = None,
) -> dict:
    """Audit all manifest pages concurrently while keeping Markdown out of graph state."""
    page_refs = state.get("source_page_refs")
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    expected_page_count = state.get("expected_page_count")
    if not isinstance(book_id, str) or not isinstance(extraction_run_id, str):
        raise ValueError("the ingestion run has no book or extraction identity")
    base_state: graphs.ingestion.state.IngestionInventoryState = {
        "book_id": book_id,
        "extraction_run_id": extraction_run_id,
        "extraction_status": "done",
        "expected_page_count": expected_page_count,
        "page_refs": page_refs,
    }
    _, _, validated_refs = inventory.domain.validate_request(base_state)
    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.PAGE_AUDIT_CONCURRENCY)

    async def audit_one(page_ref: graphs.ingestion.state.PageReference) -> tuple[dict, dict]:
        async with semaphore:
            await _report(progress_callback, kind = "page_audit", page = page_ref["page"], status = "running")
            markdown = read_page(page_ref["key"])
            if inspect.isawaitable(markdown):
                markdown = await markdown
            if not isinstance(markdown, str):
                raise ValueError(f"page {page_ref['page']} could not be read as Markdown text")
            deterministic = inventory.domain.assess_page(
                page_ref["page"], markdown, page_ref["pictures"],
            )
            run_id = state.get("agentic_run_id")
            if not isinstance(run_id, str):
                raise ValueError("the ingestion run has no agentic_run_id")
            oversized = len(markdown) > domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS
            warnings = []
            if oversized:
                proposed = deterministic
                warnings.append(
                    f"Page exceeds the {domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS}-character "
                    "audit limit; deterministic findings were retained and manual review is required."
                )
            else:
                try:
                    with domains.settings.llm_counter.service.llm_context(
                        ns = "bd", run_id = run_id, node_id = "page_auditor",
                    ):
                        result = await auditor.ainvoke({"messages": [{
                            "role": "user",
                            "content": page_audit.domain.user_payload(
                                page_ref["page"], page_ref["pictures"], markdown,
                            ),
                        }]}, config = {"callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()]})
                    if not isinstance(result, dict) or "structured_response" not in result:
                        raise ValueError("page auditor returned no structured assessment")
                    proposed, warnings = page_audit.domain.sanitize_assessment(
                        result["structured_response"],
                        page_number = page_ref["page"],
                        pictures = page_ref["pictures"],
                        markdown = markdown,
                    )
                except Exception as error:
                    logger.warning(
                        f"[bd-page-audit] page {page_ref['page']} degraded to deterministic findings "
                        f"({type(error).__name__})",
                    )
                    proposed = deterministic
                    warnings = [
                        f"Page auditor was unavailable or returned an invalid assessment ({type(error).__name__}); "
                        "deterministic findings were retained for review."
                    ]
            known_ids = {issue.issue_id for issue in deterministic.issues}
            merged_issues = list(deterministic.issues)
            merged_issues.extend(issue for issue in proposed.issues if issue.issue_id not in known_ids)
            assessment = agents.schemas.PageAssessment(
                page_number = page_ref["page"],
                page_kind = deterministic.page_kind,
                confidence = proposed.confidence,
                issues = merged_issues,
            )
            page_audit.domain.validate_assessment(
                assessment,
                page_number = page_ref["page"],
                pictures = page_ref["pictures"],
                markdown = markdown,
            )
            source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            page_number = page_ref["page"]
            output_ref = {
                "page": page_number,
                "source_key": page_ref["key"],
                "source_sha256": source_sha256,
                "normalized_key": domains.bd.ingestion.storage.keys.agentic_page_key(
                    book_id, state["agentic_run_id"], page_number,
                ),
                "assessment_key": domains.bd.ingestion.storage.keys.agentic_assessment_key(
                    book_id, state["agentic_run_id"], page_number,
                ),
                "patch_proposals_key": domains.bd.ingestion.storage.keys.agentic_patch_proposals_key(
                    book_id, state["agentic_run_id"], page_number,
                ),
                "pictures": page_ref["pictures"],
            }
            audit = {
                "page": page_number,
                "source_sha256": source_sha256,
                "markdown_chars": len(markdown),
                "pictures": page_ref["pictures"],
                "assessment": assessment.model_dump(mode = "json"),
                "warnings": warnings,
            }
            await _report(
                progress_callback,
                kind = "page_audit",
                page = page_number,
                status = "done",
                warning = bool(warnings),
            )
            return output_ref, audit

    results = await asyncio.gather(*(audit_one(page_ref) for page_ref in validated_refs))
    return {
        "page_refs": [result[0] for result in results],
        "page_audits": [result[1] for result in results],
        "status": "audited",
    }
