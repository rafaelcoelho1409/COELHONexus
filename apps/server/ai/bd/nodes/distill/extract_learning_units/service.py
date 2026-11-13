"""Persist bounded source chunks and extract cited learning units from them."""
from __future__ import annotations
import domains
from .... import agents, graphs, prompts

import hashlib
import json
import logging
from typing import Any

from pydantic import ValidationError


logger = logging.getLogger(__name__)


def _canonical_sha256(value: dict) -> str:
    body = json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _is_transient_provider_error(error: Exception) -> bool:
    return domains.settings.chat.domain.is_transient_provider_error(error)


def _log_transient_provider_failure(ref: dict[str, Any], stage: str, error: Exception) -> None:
    logger.warning(
        "[bd-distill] transient provider failure stage=%s unit=%s page=%s kind=%s error=%s",
        stage,
        ref.get("unit_id"),
        ref.get("page_number"),
        domains.settings.chat.domain.classify_error(error),
        type(error).__name__,
    )


async def prepare_units(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    storage = dependencies.storage
    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, state["book_id"])
    if source_manifest is None or source_manifest.get("status") != domains.bd.ingestion.storage.params.STATUS_DONE:
        raise ValueError("Distill requires a completed extraction")
    if source_manifest.get("extraction_id") != state["extraction_run_id"]:
        raise ValueError("the book's current extraction no longer matches this Distill run")
    source_manifest_sha256 = _canonical_sha256(source_manifest)
    if source_manifest_sha256 != state["source_manifest_sha256"]:
        raise ValueError("the source manifest changed after this Distill run was queued")

    qa_key = state["ingestion_ai_manifest_key"]
    qa_content = await storage.read_text(qa_key)
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != state["ingestion_ai_manifest_sha256"]:
        raise ValueError("the ingestion QA manifest changed after this Distill run was queued")
    qa_manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate_json(qa_content)
    if qa_manifest.status != domains.bd.ingestion.ai.schemas.AgenticRunStatus.DONE:
        raise ValueError("Distill requires a completed ingestion QA run")
    if qa_manifest.quality_status not in {"complete", "complete_with_review"}:
        raise ValueError("the selected ingestion QA run has no publishable quality result")
    if qa_manifest.extraction_run_id != state["extraction_run_id"]:
        raise ValueError("the ingestion QA run is pinned to a different extraction")
    if qa_manifest.expected_page_count != state["expected_page_count"]:
        raise ValueError("the ingestion QA page count does not match the Distill run")

    selection_by_page = None
    if state.get("content_selection_key"):
        selection_content = await storage.read_text(state["content_selection_key"])
        if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != state["content_selection_sha256"]:
            raise ValueError("the pinned content-selection revision failed its integrity check")
        selection = domains.bd.ingestion.ai.schemas.ContentSelectionManifest.model_validate_json(selection_content)
        if (
            selection.book_id != state["book_id"]
            or selection.extraction_run_id != state["extraction_run_id"]
            or selection.agentic_run_id != state["ingestion_ai_run_id"]
            or selection.revision != state["content_selection_revision"]
            or selection.expected_page_count != state["expected_page_count"]
        ):
            raise ValueError("the pinned content-selection revision does not match this Distill run")
        selection_by_page = {page.page_number: page for page in selection.pages}
        if sum(page.disposition == "include" for page in selection.pages) != state["selected_page_count"]:
            raise ValueError("the pinned content-selection count does not match this Distill run")

    page_by_number = {
        page.get("page"): page
        for page in source_manifest.get("pages", [])
        if isinstance(page, dict) and type(page.get("page")) is int
    }
    source_pages = []
    unit_refs = []
    for qa_page in qa_manifest.pages:
        content_page = selection_by_page.get(qa_page.page_number) if selection_by_page is not None else None
        if selection_by_page is not None and content_page is None:
            raise ValueError(f"content selection has no page {qa_page.page_number}")
        if content_page is not None and content_page.disposition == "exclude":
            continue
        raw_page = page_by_number.get(qa_page.page_number)
        if raw_page is None:
            raise ValueError(f"source manifest has no page {qa_page.page_number}")
        section_path = raw_page.get("section_path")
        if not isinstance(section_path, list) or not all(isinstance(item, str) for item in section_path):
            section_path = []
        source_page = {
            "page_number": qa_page.page_number,
            "source_key": qa_page.normalized_key,
            "source_sha256": qa_page.normalized_sha256,
            "section_path": section_path,
            "disposition": qa_page.delivery_disposition,
            "review_required": qa_page.status == domains.bd.ingestion.ai.schemas.AgenticPageStatus.REVIEW_REQUIRED,
            "content_role": content_page.role if content_page is not None else "unknown",
        }
        source_pages.append(source_page)
        if qa_page.delivery_disposition == "discarded_blank":
            continue
        page_text = await storage.read_text(qa_page.normalized_key)
        if hashlib.sha256(page_text.encode("utf-8")).hexdigest() != qa_page.normalized_sha256:
            raise ValueError(f"normalized source page {qa_page.page_number} failed its integrity check")
        chunks = domains.bd.distill.domain.split_source_page(
            page_text,
            page_number = qa_page.page_number,
            source_key = qa_page.normalized_key,
            source_sha256 = qa_page.normalized_sha256,
            section_path = section_path,
        )
        for ref, chunk in chunks:
            input_key = domains.bd.distill.keys.input_unit_key(state["book_id"], state["run_id"], ref["unit_id"])
            ref["input_key"] = input_key
            input_record = {
                "unit_id": ref["unit_id"],
                "page_number": ref["page_number"],
                "section_path": section_path,
                "source_key": qa_page.normalized_key,
                "source_sha256": qa_page.normalized_sha256,
                "text_sha256": ref["text_sha256"],
                "char_start": ref["char_start"],
                "char_end": ref["char_end"],
                "text": chunk,
            }
            if await storage.exists(input_key):
                existing = json.loads(await storage.read_text(input_key))
                if existing.get("text_sha256") != ref["text_sha256"]:
                    raise ValueError("an immutable Distill source-unit key has conflicting content")
            else:
                await storage.write(input_key, _json_body(input_record), content_type = domains.bd.distill.params.JSON_TYPE)
            unit_refs.append(ref)

    await graphs.service.report(
        dependencies.progress_callback,
        kind = "unit_plan",
        total_units = len(unit_refs),
    )
    return {
        "source_pages": source_pages,
        "unit_refs": unit_refs,
        "source_chapters": [
            {
                "title": chapter["title"],
                "first_page": chapter["first_page"],
                "last_page": chapter["last_page"],
            }
            for chapter in source_manifest.get("chapters", [])
            if isinstance(chapter, dict)
            and isinstance(chapter.get("title"), str)
            and type(chapter.get("first_page")) is int
            and type(chapter.get("last_page")) is int
            and chapter["first_page"] > 0
            and chapter["last_page"] >= chapter["first_page"]
        ],
        "title": str(source_manifest.get("title") or source_manifest.get("filename") or "Learning Guide"),
    }


async def extract_unit(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    ref = state["unit_ref"]
    storage = dependencies.storage
    input_record = json.loads(await storage.read_text(ref["input_key"]))
    source_text = input_record.get("text")
    if not isinstance(source_text, str) or domains.bd.distill.domain.sha256_text(source_text) != ref["text_sha256"]:
        raise ValueError(f"Distill input {ref['unit_id']} failed its integrity check")
    output_key = domains.bd.distill.keys.learning_unit_key(state["book_id"], state["run_id"], ref["unit_id"])
    if await storage.exists(output_key):
        artifact = json.loads(await storage.read_text(output_key))
        if artifact.get("text_sha256") != ref["text_sha256"]:
            raise ValueError("an immutable learning-unit artifact has conflicting source content")
        ref["learning_unit_ids"] = [item["learning_unit_id"] for item in artifact.get("lessons", [])]
        ref["existing_artifact"] = True
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "unit",
            status = "done",
            unit_id = ref["unit_id"],
            lesson_count = len(ref["learning_unit_ids"]),
        )
        return {"completed_units": [ref]}

    prompt_payload = {
        "task": "Extract the important learning units from this source chunk.",
        "physical_page": ref["page_number"],
        "section_path": ref["section_path"],
        "source_chunk_id": ref["unit_id"],
        "markdown": source_text,
    }
    extraction = None
    extraction_needs_review = False
    response_attempts = agents.distill.learning_extractor.params.MAX_STRUCTURED_RESPONSE_ATTEMPTS
    for attempt in range(response_attempts):
        result = None
        try:
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = state["run_id"], node_id = "learning_extractor", stage = "distill",
            ):
                result = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: dependencies.extractor.ainvoke({"messages": [{
                        "role": "user",
                        "content": json.dumps(prompt_payload, ensure_ascii = False),
                    }]}, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }),
                    attempts = agents.distill.learning_extractor.params.MAX_PROVIDER_ATTEMPTS,
                    wait_base_s = agents.distill.learning_extractor.params.PROVIDER_RETRY_WAIT_BASE_S,
                )
            extraction = domains.bd.distill.domain.learning_extraction_from_agent_result(result)
        except Exception as error:  # noqa: BLE001 — invalid agent output should fall back to native structured output.
            if _is_transient_provider_error(error):
                _log_transient_provider_failure(ref, "agent", error)
                raise
            extraction = None
            logger.warning(
                "[bd-distill] extractor agent failed for unit=%s attempt=%s/%s error=%s",
                ref["unit_id"],
                attempt + 1,
                response_attempts,
                type(error).__name__,
            )
        if extraction is not None:
            break

        if result is not None:
            logger.warning(
                "[bd-distill] extractor returned no valid structured response for unit=%s attempt=%s/%s",
                ref["unit_id"],
                attempt + 1,
                response_attempts,
            )
        if attempt + 1 < response_attempts:
            prompt_payload["response_retry"] = (
                "The previous response did not contain a valid LearningExtraction. Return the requested "
                "structured response now, with lessons and omitted_material fields. If the source has no "
                "useful learning material, return empty lists."
            )

    if extraction is None:
        logger.warning(
            "[bd-distill] using direct structured-output fallback for unit=%s after %s invalid agent response(s)",
            ref["unit_id"],
            response_attempts,
        )
        fallback_error: Exception | None = None
        for attempt in range(agents.distill.learning_extractor.params.MAX_DIRECT_STRUCTURED_ATTEMPTS):
            if attempt > 0:
                prompt_payload["response_retry"] = (
                    "The previous structured response was invalid. Return valid LearningExtraction JSON now. "
                    "Limit the result to four concise, high-value lessons; every lesson must retain an exact quote "
                    "from this page. If no useful lesson is supported, return empty lessons and explain omissions."
                )
            fallback_prompt = (
                prompts.distill.learning_extractor.prompts.SYSTEM_PROMPT
                + "\n\n"
                + json.dumps(prompt_payload, ensure_ascii = False)
            )
            try:
                with domains.settings.llm_counter.service.llm_context(
                    ns = "bd", run_id = state["run_id"], node_id = "learning_extractor", stage = "distill",
                ):
                    extraction, _ = await domains.settings.chat.service.resilient_ainvoke(
                        lambda: domains.settings.chat.service.chat_structured_async(
                            fallback_prompt,
                            domains.bd.distill.schemas.LearningExtraction,
                            max_tokens = agents.distill.learning_extractor.config.MODEL.max_output_tokens,
                            temperature = agents.distill.learning_extractor.config.MODEL.temperature,
                            strict = False,
                            rotator_task = agents.distill.learning_extractor.params.ROTATOR_TASK,
                        ),
                        attempts = agents.distill.learning_extractor.params.MAX_PROVIDER_ATTEMPTS,
                        wait_base_s = agents.distill.learning_extractor.params.PROVIDER_RETRY_WAIT_BASE_S,
                    )
                break
            except Exception as error:  # noqa: BLE001 — retry malformed output and transient provider failures.
                if _is_transient_provider_error(error):
                    _log_transient_provider_failure(ref, "structured_fallback", error)
                    raise
                fallback_error = error
                logger.warning(
                    "[bd-distill] structured fallback failed for unit=%s attempt=%s/%s error=%s",
                    ref["unit_id"],
                    attempt + 1,
                    agents.distill.learning_extractor.params.MAX_DIRECT_STRUCTURED_ATTEMPTS,
                    type(error).__name__,
                )

        if extraction is None:
            logger.warning(
                "[bd-distill] using JSON-text fallback for unit=%s after structured-output retries",
                ref["unit_id"],
            )
            for attempt in range(agents.distill.learning_extractor.params.MAX_TEXT_FALLBACK_ATTEMPTS):
                prompt_payload["response_retry"] = (
                    "The previous JSON-only response did not validate. Return one native JSON object with exactly "
                    "the lessons and omitted_material arrays. Every lesson must include kind, title, claim, explanation, "
                    "attribution, evidence, and confidence; each evidence item must include page_number and an exact "
                    "quote copied from this page. Use empty arrays if no supported lesson can be extracted. Do not "
                    "use markdown fences, JSON strings for arrays, or surrounding prose."
                    if attempt > 0
                    else "Return only a JSON object with exactly these keys: lessons and omitted_material. "
                    "Do not use markdown fences or add prose. Every lesson must cite an exact quote from this page."
                )
                text_prompt = (
                    prompts.distill.learning_extractor.prompts.SYSTEM_PROMPT
                    + "\n\n"
                    + json.dumps(prompt_payload, ensure_ascii = False)
                )
                try:
                    with domains.settings.llm_counter.service.llm_context(
                        ns = "bd", run_id = state["run_id"], node_id = "learning_extractor", stage = "distill",
                    ):
                        response, _ = await domains.settings.chat.service.resilient_ainvoke(
                            lambda: domains.settings.chat.service.chat_text_async(
                                text_prompt,
                                max_tokens = agents.distill.learning_extractor.config.MODEL.max_output_tokens,
                                temperature = agents.distill.learning_extractor.config.MODEL.temperature,
                                rotator_task = agents.distill.learning_extractor.params.ROTATOR_TASK,
                            ),
                            attempts = agents.distill.learning_extractor.params.MAX_PROVIDER_ATTEMPTS,
                            wait_base_s = agents.distill.learning_extractor.params.PROVIDER_RETRY_WAIT_BASE_S,
                        )
                    extraction = domains.bd.distill.domain.learning_extraction_from_agent_result({
                        "messages": [{"role": "assistant", "content": response}],
                    })
                    if extraction is None:
                        raise ValueError("the text fallback did not contain a valid LearningExtraction")
                    break
                except Exception as error:  # noqa: BLE001 — preserve the source chunk rather than silently skipping it.
                    if _is_transient_provider_error(error):
                        _log_transient_provider_failure(ref, "text_fallback", error)
                        raise
                    fallback_error = error
                    logger.warning(
                        "[bd-distill] JSON-text fallback failed for unit=%s attempt=%s/%s error=%s",
                        ref["unit_id"],
                        attempt + 1,
                        agents.distill.learning_extractor.params.MAX_TEXT_FALLBACK_ATTEMPTS,
                        type(error).__name__,
                    )

        if extraction is None:
            if not isinstance(fallback_error, (ValueError, ValidationError)):
                raise ValueError(
                    "the learning extractor failed after agent, structured-output, and JSON-text retries"
                ) from fallback_error
            logger.warning(
                "[bd-distill] preserving source chunk for review after invalid extraction output "
                "unit=%s page=%s error=%s",
                ref["unit_id"],
                ref["page_number"],
                type(fallback_error).__name__,
            )
            extraction = domains.bd.distill.schemas.LearningExtraction(
                lessons = [],
                omitted_material = [],
            )
            extraction_needs_review = True

    candidates: list[dict[str, Any]] = []
    review_flags: list[str] = []
    if extraction_needs_review:
        review_flags.append(
            f"Learning extraction could not validate a model response for source chunk {ref['unit_id']} "
            f"on page {ref['page_number']}; review the original page before relying on this section."
        )
    for lesson in extraction.lessons:
        evidence = [
            quote.model_dump(mode = "json")
            for quote in lesson.evidence
            if quote.page_number == ref["page_number"] and domains.bd.distill.domain.quote_is_supported(
                quote.quote, source_text,
            )
        ]
        if not evidence:
            review_flags.append("A proposed lesson had no exact quotation in its source chunk and was excluded.")
            continue
        candidates.append({
            "kind": lesson.kind,
            "title": lesson.title,
            "claim": lesson.claim,
            "explanation": lesson.explanation,
            "attribution": lesson.attribution,
            "evidence": evidence,
            "confidence": lesson.confidence,
        })

    if candidates:
        review_prompt = (
            prompts.distill.evidence_verifier.prompts.SYSTEM_PROMPT
            + "\n\nReview these proposed lessons against their quoted source passages. Return one verdict for each zero-based lesson_index.\n"
            + json.dumps({"page_number": ref["page_number"], "lessons": candidates}, ensure_ascii = False)
        )
        try:
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = state["run_id"], node_id = "evidence_verifier", stage = "distill",
            ):
                review, _ = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: domains.settings.chat.service.chat_structured_async(
                        review_prompt,
                        domains.bd.distill.schemas.EvidenceReview,
                        max_tokens = 2500,
                        temperature = 0.0,
                        strict = False,
                        rotator_task = "bd_distill_evidence_verifier",
                    ),
                    attempts = agents.distill.learning_extractor.params.MAX_PROVIDER_ATTEMPTS,
                    wait_base_s = agents.distill.learning_extractor.params.PROVIDER_RETRY_WAIT_BASE_S,
                )
            verdicts = {item.lesson_index: item for item in review.verdicts}
        except Exception as error:  # noqa: BLE001 — unavailable semantic review must retain a review-marked citation.
            verdicts = {}
            if _is_transient_provider_error(error):
                _log_transient_provider_failure(ref, "evidence_verifier", error)
            review_flags.append(f"Evidence review was unavailable ({type(error).__name__}); supported quotes require human review.")
    else:
        verdicts = {}

    persisted_lessons = []
    for index, candidate in enumerate(candidates):
        verdict = verdicts.get(index)
        if verdict is not None and not verdict.supported:
            review_flags.append("An evidence reviewer rejected a proposed lesson as unsupported; it was excluded.")
            continue
        lesson_id = domains.bd.distill.domain.stable_id(ref["unit_id"], str(index), candidate["claim"])
        persisted_lessons.append({
            "learning_unit_id": lesson_id,
            **candidate,
            "verification_status": "verified" if verdict is not None else "unverified",
            "verification_note": verdict.reason if verdict is not None else None,
        })
        if verdict is None:
            review_flags.append("A lesson has a source quote but no independent semantic evidence verdict.")

    artifact = {
        "unit_id": ref["unit_id"],
        "page_number": ref["page_number"],
        "source_key": input_record["source_key"],
        "section_path": ref["section_path"],
        "source_sha256": ref["source_sha256"],
        "text_sha256": ref["text_sha256"],
        "source_char_range": [ref["char_start"], ref["char_end"]],
        "extraction_status": "needs_review" if extraction_needs_review else "complete",
        "lessons": persisted_lessons,
        "omitted_material": extraction.omitted_material,
        "review_flags": sorted(set(review_flags)),
    }
    await storage.write(output_key, _json_body(artifact), content_type = domains.bd.distill.params.JSON_TYPE)
    ref["learning_unit_ids"] = [item["learning_unit_id"] for item in persisted_lessons]
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "unit",
        status = "done",
        unit_id = ref["unit_id"],
        lesson_count = len(persisted_lessons),
    )
    return {"completed_units": [ref]}


async def extract_unit_batch(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Extract every unit assigned to one fan-out graph invocation."""
    completed = []
    for ref in state.get("unit_batch", []):
        output = await extract_unit(dependencies, {**state, "unit_ref": ref})
        completed.extend(output.get("completed_units", []))
    return {"completed_units": completed}
