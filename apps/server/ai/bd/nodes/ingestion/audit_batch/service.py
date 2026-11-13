"""Read-only coordination for bounded full-book page audits."""
from __future__ import annotations
import domains
from . import domain
from .... import agents, graphs, skills

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any, Protocol


PageReader = Callable[[str], str | None | Awaitable[str | None]]
logger = logging.getLogger(__name__)


class Auditor(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class Verifier(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def verify_findings(
    verifier: Verifier,
    *,
    run_id: str,
    page_number: int,
    markdown: str,
    issues: list[agents.schemas.PageIssue],
) -> tuple[list[agents.schemas.PageIssue], list[str]]:
    """Have a second reader check one page's judgement-call findings; fail safe by keeping every finding."""
    candidates = domain.select_candidates(issues)
    if not candidates:
        return issues, []
    try:
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd", run_id = run_id, node_id = "finding_verifier",
        ):
            result = await verifier.ainvoke({"messages": [{
                "role": "user",
                "content": domain.build_verification_payload(page_number, candidates, markdown),
            }]}, config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            })
        return domain.apply_checks(issues, candidates, domain.extract_checks(result))
    except Exception as error:
        # An unreadable or failed second opinion is no reason to lose a finding: the reviewer still sees it.
        return issues, [
            f"The finding verifier was unavailable ({type(error).__name__}); the findings were kept for review."
        ]


async def audit_page(
    page_ref: graphs.ingestion.entities.PageReference,
    markdown: str,
    auditor: Auditor,
    *,
    run_id: str,
    verifier: Verifier | None = None,
) -> dict[str, Any]:
    """Audit one page: the deterministic inventory, the model's findings (retried, then checked by a second reader) and
    their merge. A model that fails every attempt degrades to the deterministic findings instead of raising, and the
    result says so in `audit_mode`. The full-book audit and the auditor evaluation both call this."""
    deterministic = domains.bd.ingestion.ai.inventory.domain.assess_page(
        page_ref["page"], markdown, page_ref["pictures"],
    )
    oversized = (
        domains.bd.ingestion.ai.inventory.domain.effective_length(markdown)
        > domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS
    )
    warnings = []
    deterministic_only = deterministic.page_kind in {
        agents.schemas.PageKind.BLANK,
        agents.schemas.PageKind.IMAGE_ONLY,
    }
    structured_output_fallback = False
    if deterministic_only:
        proposed = deterministic
        audit_mode = "deterministic_only"
    elif oversized:
        proposed = deterministic
        audit_mode = "deterministic_limit"
        warnings.append(
            f"Page exceeds the {domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS}-character "
            "audit limit; deterministic findings were retained and manual review is required."
        )
    else:
        audit_error: Exception | None = None
        for _ in range(domains.bd.ingestion.ai.params.PAGE_AUDIT_ATTEMPTS):
            try:
                with domains.settings.llm_counter.service.llm_context(
                    ns = "bd", run_id = run_id, node_id = "page_auditor",
                ):
                    result = await auditor.ainvoke({"messages": [{
                        "role": "user",
                        "content": domain.user_payload(
                            page_ref["page"], page_ref["pictures"], markdown,
                        ),
                    }], "files": skills.service.files_for("page_auditor")}, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    })
                response = domain.extract_assessment_response(result)
                proposed, warnings = domain.sanitize_assessment(
                    response,
                    page_number = page_ref["page"],
                    pictures = page_ref["pictures"],
                    markdown = markdown,
                )
                audit_mode = "llm_reviewed"
                audit_error = None
                if verifier is not None:
                    checked_issues, check_warnings = await verify_findings(
                        verifier,
                        run_id = run_id,
                        page_number = page_ref["page"],
                        markdown = markdown,
                        issues = proposed.issues,
                    )
                    proposed = proposed.model_copy(update = {"issues": checked_issues})
                    warnings = warnings + check_warnings
                break
            except Exception as error:
                audit_error = error
        if audit_error is not None:
            logger.warning(
                f"[bd-page-audit] page {page_ref['page']} degraded to deterministic findings "
                f"({type(audit_error).__name__})",
            )
            proposed = deterministic
            audit_mode = "deterministic_fallback"
            structured_output_fallback = True
            warnings = [
                f"Page auditor was unavailable or returned an invalid assessment ({type(audit_error).__name__}); "
                "deterministic findings were retained for review."
            ]
    merged_issues = domains.bd.ingestion.ai.inventory.domain.merge_issues(
        deterministic.issues,
        proposed.issues,
        markdown)
    assessment = agents.schemas.PageAssessment(
        page_number = page_ref["page"],
        page_kind = deterministic.page_kind,
        confidence = proposed.confidence,
        issues = merged_issues,
    )
    domain.validate_assessment(
        assessment,
        page_number = page_ref["page"],
        pictures = page_ref["pictures"],
        markdown = markdown,
    )
    return {
        "assessment": assessment,
        "warnings": warnings,
        "audit_mode": audit_mode,
        "structured_output_fallback": structured_output_fallback,
    }


async def audit_pages(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    auditor: Auditor,
    progress_callback = None,
    verifier: Verifier | None = None,
) -> dict:
    """Audit all manifest pages concurrently while keeping Markdown out of graph state."""
    page_refs = state.get("source_page_refs")
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    expected_page_count = state.get("expected_page_count")
    if not isinstance(book_id, str) or not isinstance(extraction_run_id, str):
        raise ValueError("the ingestion run has no book or extraction identity")
    request = domains.bd.ingestion.ai.schemas.ExtractionRequest(
        book_id = book_id,
        extraction_run_id = extraction_run_id,
        extraction_status = "done",
        expected_page_count = expected_page_count,
        page_refs = page_refs,
    )
    section_paths = {
        page_ref["page"]: page_ref.get("section_path", [])
        for page_ref in page_refs
    }
    validated_refs = [
        {**page_ref, "section_path": section_paths.get(page_ref["page"], [])}
        for page_ref in request.page_references()
    ]
    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.PAGE_AUDIT_CONCURRENCY)

    async def audit_one(page_ref: graphs.ingestion.entities.PageReference) -> tuple[dict, dict]:
        async with semaphore:
            await graphs.service.report(progress_callback, kind = "page_audit", page = page_ref["page"], status = "running")
            markdown = read_page(page_ref["key"])
            if inspect.isawaitable(markdown):
                markdown = await markdown
            if not isinstance(markdown, str):
                raise ValueError(f"page {page_ref['page']} could not be read as Markdown text")
            run_id = state.get("agentic_run_id")
            if not isinstance(run_id, str):
                raise ValueError("the ingestion run has no agentic_run_id")
            audited = await audit_page(page_ref, markdown, auditor, run_id = run_id, verifier = verifier)
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
                "assessment": audited["assessment"].model_dump(mode = "json"),
                "warnings": audited["warnings"],
                "audit_mode": audited["audit_mode"],
                "structured_output_fallback": audited["structured_output_fallback"],
            }
            await graphs.service.report(
                progress_callback,
                kind = "page_audit",
                page = page_number,
                status = "done",
                warning = bool(audited["warnings"]),
            )
            return output_ref, audit

    results = await _gather_cancel_on_failure(audit_one(page_ref) for page_ref in validated_refs)
    return {
        "page_refs": [result[0] for result in results],
        "page_audits": [result[1] for result in results],
        "status": "audited",
    }


async def _gather_cancel_on_failure(awaitables: Iterable[Coroutine[Any, Any, Any]]) -> list[Any]:
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions = True)
        raise
