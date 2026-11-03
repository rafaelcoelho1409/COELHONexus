"""Specialist dispatch, safe patching, and page artifact persistence."""
from __future__ import annotations
import domains
from . import domain
from .... import agents, graphs

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]


class Specialist(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


async def repair_pages(
    state: graphs.ingestion.state.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    specialists: dict[str, Specialist],
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
    ) -> tuple[list[agents.schemas.PatchProposal], str | None]:
        role = item["role"]
        assigned_ids = [entry["issue_id"] for entry in item["issue_refs"]]
        assigned = {issue_id: issues[issue_id] for issue_id in assigned_ids}
        payload = {
            "task": "Propose minimal evidence-backed patches only for these assigned issues.",
            "page_number": page_number,
            "source_sha256": source_sha256,
            "role": role,
            "markdown": markdown,
            "assigned_issues": [issue.model_dump(mode = "json") for issue in assigned.values()],
        }
        try:
            response = await specialists[role].ainvoke({"messages": [{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii = False),
            }]})
            if not isinstance(response, dict) or "structured_response" not in response:
                raise ValueError("specialist returned no structured PatchProposalBatch")
            proposals = domain.validate_proposals(
                response["structured_response"],
                page_number = page_number,
                source_sha256 = source_sha256,
                markdown = markdown,
                assigned_issues = assigned,
                role = agents.schemas.SpecialistRole(role),
            )
            return proposals, None
        except Exception as error:
            return [], f"{role}: {str(error)[:500]}"

    async def process(page_ref: graphs.ingestion.state.IngestionPageReference) -> dict:
        async with semaphore:
            page_number = page_ref["page"]
            markdown = read_page(page_ref["source_key"])
            if inspect.isawaitable(markdown):
                markdown = await markdown
            if not isinstance(markdown, str):
                raise ValueError(f"page {page_number} could not be read as Markdown text")
            source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            if source_sha256 != page_ref["source_sha256"]:
                raise ValueError(f"source page {page_number} changed after its quality audit")
            if len(markdown) > domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS:
                raise ValueError(f"page {page_number} exceeds the configured repair limit")

            issue_list = agents.schemas.PageAssessment.model_validate(
                audits[page_number]["assessment"],
            ).issues
            issues = {issue.issue_id: issue for issue in issue_list}
            assigned_items = work_by_page.get(page_number, [])
            responses = await asyncio.gather(*(
                run_specialist(item, markdown, page_number, source_sha256, issues)
                for item in assigned_items
            ))
            proposals = [proposal for batch, _ in responses for proposal in batch]
            errors = [error for _, error in responses if error]
            try:
                normalized, auto_applied, proposal_reviews = domain.apply_proposals(markdown, proposals)
            except ValueError as error:
                normalized = markdown
                auto_applied = []
                proposal_reviews = [proposal.issue_id for proposal in proposals]
                errors.append(str(error))

            represented = {proposal.issue_id for proposal in proposals}
            assigned_ids = {
                ref["issue_id"]
                for item in assigned_items
                for ref in item["issue_refs"]
            }
            unresolved = (assigned_ids - represented) | set(proposal_reviews)
            unsupported = set(issues) - assigned_ids
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
                "errors": errors,
            }
            proposal_content = json.dumps(
                proposal_payload, ensure_ascii = False, separators = (",", ":"), sort_keys = True,
            )
            await _write(write_artifact, page_ref["normalized_key"], normalized, "text/markdown; charset=utf-8")
            await _write(write_artifact, page_ref["assessment_key"], assessment_content, "application/json")
            await _write(write_artifact, page_ref["patch_proposals_key"], proposal_content, "application/json")
            return {
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
                "status": "review_required" if review_ids or errors else "done",
                "error": "; ".join(errors) if errors else "",
            }

    page_results = await asyncio.gather(*(process(page_ref) for page_ref in page_refs))
    return {"page_results": page_results, "status": "repaired"}


async def _write(writer: ArtifactWriter, key: str, content: str, content_type: str) -> None:
    result = writer(key, content, content_type)
    if inspect.isawaitable(result):
        await result
