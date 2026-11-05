"""Specialist dispatch, safe patching, and page artifact persistence."""
from __future__ import annotations
import domains
from . import domain
from .... import agents, graphs, skills
from .. import inventory

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any, Protocol


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]


class Specialist(Protocol):
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


async def repair_pages(
    state: graphs.ingestion.state.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    specialists: dict[str, Specialist],
    progress_callback = None,
) -> dict:
    """Run bounded specialists, apply only unambiguous low-risk proposals, and persist every page."""
    page_refs = state["page_refs"]
    audits = {page["page"]: page for page in state["page_audits"]}
    work_by_page: dict[int, list[dict[str, object]]] = {}
    for item in state.get("work_items", []):
        work_by_page.setdefault(item["page"], []).append(item)
    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.PAGE_REPAIR_CONCURRENCY)

    async def run_specialist(
        item: dict[str, object],
        markdown: str,
        page_number: int,
        source_sha256: str,
        issues: dict[str, agents.schemas.PageIssue],
    ) -> tuple[list[agents.schemas.PatchProposal], str | None, int, int]:
        role = item["role"]
        assigned_ids = [entry["issue_id"] for entry in item["issue_refs"]]
        assigned = {issue_id: issues[issue_id] for issue_id in assigned_ids}
        source_context = domain.build_repair_context(
            markdown,
            list(assigned.values()),
            per_issue_chars = domains.bd.ingestion.ai.params.REPAIR_CONTEXT_CHARS_PER_ISSUE,
            max_chars = domains.bd.ingestion.ai.params.MAX_REPAIR_CONTEXT_CHARS,
        )
        if not source_context:
            return [], f"{role}: assigned issue evidence was not found in the source page", 0, 0
        payload = {
            "task": "Propose minimal evidence-backed patches only within the supplied source context for these assigned issues.",
            "role": role,
            "markdown": source_context,
            "assigned_issues": [{
                "issue_id": issue.issue_id,
                "code": issue.code.value,
                "severity": issue.severity.value,
                "description": issue.description,
                "evidence_excerpts": [evidence.excerpt for evidence in issue.evidence],
            } for issue in assigned.values()],
        }
        run_id = state.get("agentic_run_id")
        if not isinstance(run_id, str):
            return [], f"{role}: the ingestion run has no agentic_run_id", len(source_context), 0
        node_id = "structure_repair" if role == "structure" else "text_repair"
        skill_role = "structure_repair" if role == "structure" else role
        last_error = ""
        attempts = 0
        for attempt in range(1, domains.bd.ingestion.ai.params.SPECIALIST_ATTEMPTS + 1):
            attempts = attempt
            request = payload if not last_error else {
                **payload,
                "previous_attempt_rejected": last_error,
                "response_contract": "Return PatchDraftBatch JSON with a `proposals` array; copy target_text verbatim from `markdown`.",
            }
            try:
                with domains.settings.llm_counter.service.llm_context(
                    ns = "bd", run_id = run_id, node_id = node_id,
                ):
                    response = await specialists[role].ainvoke({"messages": [{
                        "role": "user",
                        "content": json.dumps(request, ensure_ascii = False),
                    }], "files": skills.service.files_for(skill_role)}, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    })
                structured = domain.extract_patch_response(response, role = role)
                proposals = domain.validate_proposals(
                    structured,
                    page_number = page_number,
                    source_sha256 = source_sha256,
                    markdown = markdown,
                    assigned_issues = assigned,
                    role = agents.schemas.SpecialistRole(role),
                    allowed_context = source_context,
                )
                return proposals, None, len(source_context) * attempts, attempts
            except Exception as error:
                last_error = str(error)[:300]
        return [], f"{role}: {last_error}", len(source_context) * attempts, attempts

    async def process(page_ref: graphs.ingestion.state.IngestionPageReference) -> dict:
        async with semaphore:
            page_number = page_ref["page"]
            await _report(progress_callback, kind = "page_repair", page = page_number, status = "running")
            markdown = read_page(page_ref["source_key"])
            if inspect.isawaitable(markdown):
                markdown = await markdown
            if not isinstance(markdown, str):
                raise ValueError(f"page {page_number} could not be read as Markdown text")
            source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            if source_sha256 != page_ref["source_sha256"]:
                raise ValueError(f"source page {page_number} changed after its quality audit")
            oversized = (
                inventory.domain.effective_length(markdown)
                > domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS
            )

            issue_list = agents.schemas.PageAssessment.model_validate(
                audits[page_number]["assessment"],
            ).issues
            issues = {issue.issue_id: issue for issue in issue_list}
            _, _, mechanically_resolved_before_repair = domain.apply_mechanical_fixes_for_issues(
                markdown,
                issue_list,
            )
            mechanically_resolved = set(mechanically_resolved_before_repair)
            assigned_items = []
            for item in work_by_page.get(page_number, []):
                remaining_refs = [
                    reference for reference in item["issue_refs"]
                    if reference["issue_id"] not in mechanically_resolved
                ]
                if remaining_refs:
                    assigned_items.append({**item, "issue_refs": remaining_refs})
            responses = [] if oversized else await asyncio.gather(*(
                run_specialist(item, markdown, page_number, source_sha256, issues)
                for item in assigned_items
            ))
            proposals = [proposal for batch, _, _, _ in responses for proposal in batch]
            audit_warnings = list(audits[page_number].get("warnings", []))
            errors = [error for _, error, _, _ in responses if error]
            if oversized:
                errors.append(
                    f"Page exceeds the {domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS}-character "
                    "repair limit; the original Markdown was preserved for manual review."
                )
            try:
                normalized, auto_applied, proposal_reviews = domain.apply_proposals(markdown, proposals)
            except ValueError as error:
                normalized = markdown
                auto_applied = []
                proposal_reviews = [proposal.issue_id for proposal in proposals]
                errors.append(str(error))

            # Mechanical normalization follows model patches, so specialist anchors always refer to the source page.
            normalized, mechanical_counts, mechanical_ids = domain.apply_mechanical_fixes_for_issues(
                normalized,
                issue_list,
            )
            auto_applied = auto_applied + sorted(mechanical_ids)

            represented = {proposal.issue_id for proposal in proposals}
            assigned_ids = {
                ref["issue_id"]
                for item in assigned_items
                for ref in item["issue_refs"]
            }
            unresolved = (assigned_ids - represented) | set(proposal_reviews)
            unsupported = set(issues) - assigned_ids - set(mechanical_ids)
            review_ids = sorted(unresolved | unsupported)
            normalized_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            assessment_content = json.dumps(
                audits[page_number]["assessment"], ensure_ascii = False,
                separators = (",", ":"), sort_keys = True,
            )
            proposal_payload = {
                "page_number": page_number,
                "source_sha256": source_sha256,
                "proposals": [proposal.model_dump(mode = "json") for proposal in proposals],
                "auto_applied_issue_ids": auto_applied,
                "review_required_issue_ids": review_ids,
                "mechanical_fixes": mechanical_counts,
                "audit_warnings": audit_warnings,
                "errors": errors,
            }
            proposal_content = json.dumps(
                proposal_payload, ensure_ascii = False, separators = (",", ":"), sort_keys = True,
            )
            await _write(write_artifact, page_ref["normalized_key"], normalized, "text/markdown; charset=utf-8")
            await _write(write_artifact, page_ref["assessment_key"], assessment_content, "application/json")
            await _write(write_artifact, page_ref["patch_proposals_key"], proposal_content, "application/json")
            result = {
                "page": page_number,
                "source_sha256": source_sha256,
                "normalized_sha256": normalized_hash,
                "assessment_sha256": hashlib.sha256(assessment_content.encode("utf-8")).hexdigest(),
                "patch_proposals_sha256": hashlib.sha256(proposal_content.encode("utf-8")).hexdigest(),
                "normalized_key": page_ref["normalized_key"],
                "assessment_key": page_ref["assessment_key"],
                "patch_proposals_key": page_ref["patch_proposals_key"],
                "assessment": audits[page_number]["assessment"],
                "patch_proposal_count": len(proposals),
                "auto_applied_issue_ids": auto_applied,
                "review_required_issue_ids": review_ids,
                "delivery_disposition": (
                    "discarded_blank"
                    if audits[page_number]["assessment"].get("page_kind") == "blank"
                    else "image_only"
                    if audits[page_number]["assessment"].get("page_kind") == "image_only"
                    else "include"
                ),
                "repair_context_chars": sum(context_chars for _, _, context_chars, _ in responses),
                "repair_call_count": sum(attempts for _, _, _, attempts in responses),
                "mechanical_fix_counts": mechanical_counts,
                "audit_warnings": audit_warnings,
                "status": "review_required" if review_ids or errors else "done",
                "error": "; ".join(errors) if errors else "",
            }
            await _report(progress_callback, kind = "page_repair", page = page_number, status = "done")
            return result

    page_results = await _gather_cancel_on_failure(process(page_ref) for page_ref in page_refs)
    return {"page_results": page_results, "status": "repaired"}


async def _write(writer: ArtifactWriter, key: str, content: str, content_type: str) -> None:
    result = writer(key, content, content_type)
    if inspect.isawaitable(result):
        await result


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
