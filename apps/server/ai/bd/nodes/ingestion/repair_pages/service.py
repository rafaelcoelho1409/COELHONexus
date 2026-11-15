"""Specialist dispatch, safe patching, and page artifact persistence."""
from __future__ import annotations
import domains
from . import domain
from .... import agents, graphs, skills

import asyncio
import base64
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any, Protocol

from langchain_core.messages import HumanMessage
from pydantic import ValidationError


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]


class Specialist(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        context: domains.bd.ingestion.ai.entities.SpecialistContext | None = None,
    ) -> dict[str, Any]: ...


class PatchVerifier(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class BookIndex:
    """The book's source pages for the specialists' lookup tools: read once, and only if a specialist asks."""

    def __init__(self, read_page: PageReader, page_refs: list[dict[str, Any]]):
        self._read_page = read_page
        self._keys = {ref["page"]: ref["source_key"] for ref in page_refs}
        self._pages: dict[int, str] | None = None
        self._lock = asyncio.Lock()

    async def _all(self) -> dict[int, str]:
        async with self._lock:
            if self._pages is None:
                async def read(key: str) -> str:
                    text = self._read_page(key)
                    if inspect.isawaitable(text):
                        text = await text
                    return text if isinstance(text, str) else ""

                numbers = sorted(self._keys)
                texts = await asyncio.gather(*(read(self._keys[number]) for number in numbers))
                self._pages = dict(zip(numbers, texts))
            return self._pages

    async def term_counts(self, variants: list[str]) -> str:
        pages = await self._all()
        return domain.count_variants(
            "\n".join(pages[number] for number in sorted(pages)),
            variants,
            max_variants = domains.bd.ingestion.ai.params.MAX_TERM_VARIANTS,
            max_chars = domains.bd.ingestion.ai.params.MAX_TERM_CHARS,
        )

    async def page_edges(self, page_number: int, lines: int) -> str:
        params = domains.bd.ingestion.ai.params
        lines = max(1, min(lines, params.EDGE_LINES_MAX))
        pages = await self._all()
        parts = []
        for label, number, from_end in (
            ("previous", page_number - 1, True),
            ("next", page_number + 1, False),
        ):
            if number not in pages:
                parts.append(f"{label} page: none")
                continue
            shown = domain.edge_lines(pages[number], lines, from_end = from_end, max_chars = params.EDGE_LINE_CHARS)
            heading = f"{label} page ({number}), {'last' if from_end else 'first'} lines:"
            parts.append("\n".join([heading, *shown]) if shown else f"{label} page ({number}): empty")
        return "\n\n".join(parts)


async def verify_source_patches(
    proposals: list[agents.schemas.PatchProposal],
    *,
    markdown: str,
    page_number: int,
    run_id: str,
    verifier: PatchVerifier | None,
    read_source_page_image: graphs.ingestion.entities.SourcePageImageReader | None,
) -> tuple[set[str], list[dict[str, object]], list[str], int]:
    """Confirm eligible corrections against the original page image; any doubt leaves text unchanged."""
    candidates = domain.source_verification_candidates(markdown, proposals)
    if not candidates:
        return set(), [], [], 0

    def unavailable(reason: str) -> tuple[set[str], list[dict[str, object]], list[str], int]:
        return set(), [{
            "issue_id": proposal.issue_id,
            "status": "unavailable",
            "reason": reason,
        } for proposal in candidates], [
            f"{len(candidates)} text patch candidate(s) were left unchanged because source-page verification was unavailable.",
        ], len(candidates)

    if verifier is None or read_source_page_image is None:
        return unavailable("visual verifier or source-page renderer is unavailable")

    try:
        image_result = read_source_page_image(page_number)
        if inspect.isawaitable(image_result):
            image_result = await image_result
    except Exception as error:
        return unavailable(f"original page image could not be read ({type(error).__name__})")
    if not isinstance(image_result, bytes) or not image_result:
        return unavailable("original page image could not be rendered")

    image_sha256 = hashlib.sha256(image_result).hexdigest()
    image_url = "data:image/jpeg;base64," + base64.b64encode(image_result).decode("ascii")

    class InvalidVerifierResponse(ValueError):
        """The verifier returned a structurally valid but mismatched batch."""

    def validation_reason(error: ValidationError) -> str:
        """Expose schema locations without putting book text or model input in artifacts."""
        try:
            locations = sorted({
                ".".join(str(part) for part in item.get("loc", ())) or "response"
                for item in error.errors(include_input = False)
            })
        except TypeError:
            locations = []
        suffix = f" at {', '.join(locations[:4])}" if locations else ""
        return f"visual verifier response failed schema validation{suffix}"

    async def verify_batch(
        batch_candidates: list[agents.schemas.PatchProposal],
    ) -> dict[str, agents.schemas.PatchVerification]:
        def verification_item(proposal: agents.schemas.PatchProposal) -> dict[str, str]:
            target_focus, replacement_focus = domain.patch_verification_focus(proposal)
            return {
                "issue_id": proposal.issue_id,
                "issue_code": proposal.issue_code.value,
                "target_text": proposal.target_text,
                "context_before": proposal.context_before,
                "context_after": proposal.context_after,
                "proposed_replacement": proposal.replacement,
                "target_focus": target_focus,
                "replacement_focus": replacement_focus,
                "rationale": proposal.rationale,
            }

        payload = {
            "page_number": page_number,
            "source_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "patches": [verification_item(proposal) for proposal in batch_candidates],
        }
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd", run_id = run_id, node_id = "text_patch_verifier",
        ):
            # The Rotator receives text and the source image together and routes the multimodal request.
            response = await verifier.ainvoke({"messages": [HumanMessage(content = [
                {"type": "text", "text": json.dumps(payload, ensure_ascii = False)},
                {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
            ])]}, config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            })
        try:
            batch = domain.extract_patch_verification_response(response)
        except ValidationError:
            raise
        except ValueError as error:
            raise InvalidVerifierResponse(str(error)) from error
        checks = {check.issue_id: check for check in batch.verifications}
        expected_ids = {proposal.issue_id for proposal in batch_candidates}
        if len(checks) != len(batch.verifications) or set(checks) != expected_ids:
            raise InvalidVerifierResponse("visual verifier returned an incomplete or mismatched result")
        return checks

    checks: dict[str, agents.schemas.PatchVerification] = {}
    failed_checks: dict[str, str] = {}

    async def verify_with_recovery(batch_candidates: list[agents.schemas.PatchProposal]) -> None:
        try:
            checks.update(await verify_batch(batch_candidates))
        except (ValidationError, InvalidVerifierResponse) as error:
            # Isolate malformed records so one response cannot discard valid sibling checks.
            if len(batch_candidates) > 1:
                midpoint = len(batch_candidates) // 2
                await verify_with_recovery(batch_candidates[:midpoint])
                await verify_with_recovery(batch_candidates[midpoint:])
                return
            reason = validation_reason(error) if isinstance(error, ValidationError) else str(error)
            failed_checks[batch_candidates[0].issue_id] = reason
        except Exception as error:
            reason = f"visual verifier failed ({type(error).__name__})"
            failed_checks.update({proposal.issue_id: reason for proposal in batch_candidates})

    await verify_with_recovery(candidates)

    verified_issue_ids = set()
    results = []
    for proposal in candidates:
        if proposal.issue_id in failed_checks:
            results.append({
                "issue_id": proposal.issue_id,
                "status": "unavailable",
                "reason": failed_checks[proposal.issue_id],
            })
            continue
        check = checks[proposal.issue_id]
        target_focus, replacement_focus = domain.patch_verification_focus(proposal)
        source_reading = " ".join(check.source_reading.split())
        target_focus = " ".join(target_focus.split())
        replacement_focus = " ".join(replacement_focus.split())
        confirmed = domain.source_reading_confirms_patch(proposal, check)
        if confirmed:
            verified_issue_ids.add(proposal.issue_id)
            status = "confirmed"
            reason = (
                "The literal source reading matches the proposed correction; the verifier's verdict conflicted with its reading."
                if check.verdict != agents.schemas.PatchVerificationVerdict.CONFIRMED
                else check.reason
            )
        elif (
            source_reading
            and source_reading == target_focus
            and target_focus != replacement_focus
        ):
            status = "source_mismatch"
            reason = "The literal source reading matches the extracted text, so the proposed correction was not applied."
        elif check.verdict == agents.schemas.PatchVerificationVerdict.CONTRADICTED:
            status = "source_mismatch"
            reason = check.reason
        elif check.verdict == agents.schemas.PatchVerificationVerdict.CONFIRMED:
            status = "reading_mismatch"
            reason = check.reason
        else:
            status = "uncertain"
            reason = check.reason
        results.append({
            "issue_id": proposal.issue_id,
            "status": status,
            "source_reading": check.source_reading,
            "source_verdict": check.verdict.value,
            "target_focus": target_focus,
            "replacement_focus": replacement_focus,
            "reason": reason,
            "source_image_sha256": image_sha256,
        })

    unavailable_count = len(failed_checks)
    not_confirmed = len(candidates) - len(verified_issue_ids)
    warnings = []
    if unavailable_count:
        warnings.append(
            f"{unavailable_count} text patch candidate(s) were left unchanged because source verification was unavailable."
        )
    rejected_count = not_confirmed - unavailable_count
    if rejected_count:
        warnings.append(
            f"{rejected_count} text patch candidate(s) were left unchanged because the source image did not confirm them."
        )
    return verified_issue_ids, results, warnings, unavailable_count


async def repair_pages(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    specialists: dict[str, Specialist],
    progress_callback = None,
    patch_verifier: PatchVerifier | None = None,
    read_source_page_image: graphs.ingestion.entities.SourcePageImageReader | None = None,
) -> dict:
    """Run bounded specialists, apply only unambiguous low-risk proposals, and persist every page."""
    page_refs = state["page_refs"]
    audits = {page["page"]: page for page in state["page_audits"]}
    selection = state.get("content_selection")
    if not isinstance(selection, list) or len(selection) != state.get("expected_page_count"):
        raise ValueError("page repair requires a complete early content selection")
    selection_by_page = {page["page_number"]: page for page in selection}
    work_by_page: dict[int, list[dict[str, object]]] = {}
    for item in state.get("work_items", []):
        work_by_page.setdefault(item["page"], []).append(item)
    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.PAGE_REPAIR_CONCURRENCY)
    book = BookIndex(read_page, page_refs)

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
        source_context = domains.bd.ingestion.ai.inventory.domain.build_repair_context(
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
        specialist_context = domains.bd.ingestion.ai.entities.SpecialistContext(
            check_patch = lambda issue_id, risk, target_text, replacement, confidence: domain.check_draft(
                issue_id = issue_id,
                risk = agents.schemas.PatchRisk(risk),
                target_text = target_text,
                replacement = replacement,
                confidence = confidence,
                markdown = markdown,
                page_number = page_number,
                source_sha256 = source_sha256,
                assigned_issues = assigned,
                role = agents.schemas.SpecialistRole(role),
                allowed_context = source_context,
            ),
            term_counts = book.term_counts,
            page_edges = book.page_edges,
        )
        last_error = ""
        attempts = 0
        for attempt in range(1, domains.bd.ingestion.ai.params.SPECIALIST_ATTEMPTS + 1):
            attempts = attempt
            request = payload if not last_error else {
                **payload,
                "previous_attempt_rejected": last_error,
                "response_contract": "Return PatchDraftBatch JSON with a `proposals` array; copy target_text verbatim from `markdown`."
                + (" You ran out of turns: call `check_patches` once at most, then answer." if "model-call limit" in last_error else ""),
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
                    }, context = specialist_context)
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

    async def process(page_ref: graphs.ingestion.entities.IngestionPageReference) -> dict:
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
            selected = selection_by_page.get(page_number)
            if selected is None:
                raise ValueError(f"source page {page_number} has no early content-selection result")
            repair_skipped = selected.get("disposition") == "exclude"
            await graphs.service.report(
                progress_callback,
                kind = "page_repair",
                page = page_number,
                status = "running",
                repair_skipped = repair_skipped,
            )
            oversized = (
                domains.bd.ingestion.ai.inventory.domain.effective_length(markdown)
                > domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS
            ) and not repair_skipped

            issue_list = agents.schemas.PageAssessment.model_validate(
                audits[page_number]["assessment"],
            ).issues
            issues = {issue.issue_id: issue for issue in issue_list}
            _, _, mechanically_resolved_before_repair = (
                (markdown, {}, [])
                if repair_skipped
                else domain.apply_mechanical_fixes_for_issues(markdown, issue_list)
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
            responses = [] if oversized or repair_skipped else await asyncio.gather(*(
                run_specialist(item, markdown, page_number, source_sha256, issues)
                for item in assigned_items
            ))
            proposals = [proposal for batch, _, _, _ in responses for proposal in batch]
            audit_warnings = list(audits[page_number].get("warnings", []))
            errors = [error for _, error, _, _ in responses if error]
            source_verified_ids, source_verification_results, source_verification_warnings, source_verification_unavailable_count = (
                await verify_source_patches(
                    proposals,
                    markdown = markdown,
                    page_number = page_number,
                    run_id = state["agentic_run_id"],
                    verifier = patch_verifier,
                    read_source_page_image = read_source_page_image,
                )
            )
            audit_warnings.extend(source_verification_warnings)
            if oversized:
                errors.append(
                    f"Page exceeds the {domains.bd.ingestion.ai.params.MAX_PAGE_MARKDOWN_CHARS}-character "
                    "repair limit; the original Markdown was preserved and the run continued."
                )
            if repair_skipped:
                normalized, auto_applied, proposal_reviews = markdown, [], []
            else:
                try:
                    normalized, auto_applied, proposal_reviews = domain.apply_proposals(
                        markdown,
                        proposals,
                        source_verified_issue_ids = source_verified_ids,
                    )
                except ValueError as error:
                    normalized = markdown
                    auto_applied = []
                    proposal_reviews = [proposal.issue_id for proposal in proposals]
                    errors.append(str(error))

            # Mechanical normalization follows model patches, so specialist anchors always refer to the source page.
            normalized, mechanical_counts, mechanical_ids = (
                (normalized, {}, [])
                if repair_skipped
                else domain.apply_mechanical_fixes_for_issues(normalized, issue_list)
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
            review_ids = [] if repair_skipped else sorted(unresolved | unsupported)
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
                "source_verification_results": source_verification_results,
                "repair_skipped_reason": (
                    "page excluded by the pre-repair content selection" if repair_skipped else ""
                ),
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
                "content_role": selected["role"],
                "content_confidence": selected["confidence"],
                "content_rationale": selected["rationale"],
                "content_disposition": selected["disposition"],
                "content_decision_source": selected["decision_source"],
                "repair_skipped_reason": (
                    "page excluded by the pre-repair content selection" if repair_skipped else ""
                ),
                "source_verified_issue_ids": sorted(source_verified_ids),
                "source_verification_results": source_verification_results,
                "source_verification_unavailable_count": source_verification_unavailable_count,
            }
            await graphs.service.report(
                progress_callback,
                kind = "page_repair",
                page = page_number,
                status = "done",
                repair_skipped = repair_skipped,
            )
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
