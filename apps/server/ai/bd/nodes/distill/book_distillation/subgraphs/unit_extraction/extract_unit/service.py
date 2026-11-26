"""Extract and persist source-cited lesson candidates from bounded source chunks."""
from __future__ import annotations
import domains
from ....... import agents, graphs, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def _invoke_learning_extractor(
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
    run_id: str,
    payload: dict[str, Any],
    extractor_files: dict[str, Any],
) -> Any:
    with domains.settings.llm_counter.service.llm_context(
        ns = "bd", run_id = run_id, node_id = "learning_extractor", stage = "distill",
    ):
        return await domains.settings.chat.service.resilient_ainvoke(
            lambda: dependencies.learning_extractor.ainvoke({
                "messages": [{
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii = False),
                }],
                "files": extractor_files,
            }, config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            }),
            attempts = domains.bd.distill.ai.params.AGENT_CALL_ATTEMPTS,
            wait_base_s = domains.bd.distill.ai.params.AGENT_CALL_RETRY_WAIT_BASE_S,
        )


def _candidate_artifact_from_learning_unit(ref: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    """Recover candidates from a completed artifact created before extraction was split from verification."""
    candidates = []
    for lesson in artifact.get("lessons", []):
        if not isinstance(lesson, dict):
            continue
        candidates.append({
            "learning_unit_id": lesson.get("learning_unit_id"),
            "kind": lesson.get("kind"),
            "title": lesson.get("title"),
            "claim": lesson.get("claim"),
            "explanation": lesson.get("explanation"),
            "attribution": lesson.get("attribution"),
            "evidence": lesson.get("evidence", []),
            "confidence": lesson.get("confidence", 0.5),
        })
    return {
        "unit_id": ref["unit_id"],
        "page_number": artifact.get("page_number", ref["page_number"]),
        "processed_page_number": artifact.get("processed_page_number"),
        "source_key": artifact.get("source_key"),
        "section_path": artifact.get("section_path", ref["section_path"]),
        "source_sha256": artifact.get("source_sha256", ref["source_sha256"]),
        "text_sha256": artifact["text_sha256"],
        "source_char_range": artifact.get("source_char_range", [ref["char_start"], ref["char_end"]]),
        "extraction_status": artifact.get("extraction_status", "complete"),
        "candidates": candidates,
        "omitted_material": artifact.get("omitted_material", []),
        "review_flags": artifact.get("review_flags", []),
    }


async def extract_unit(
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Run only the learning extractor; evidence verification is a separate graph stage."""
    ref = state["unit_ref"]
    storage = dependencies.storage
    input_record = json.loads(await storage.read_text(ref["input_key"]))
    source_text = input_record.get("text")
    if not isinstance(source_text, str) or domains.bd.distill.ai.domain.sha256_text(source_text) != ref["text_sha256"]:
        raise ValueError(f"Distill input {ref['unit_id']} failed its integrity check")

    candidate_key = domains.bd.distill.ai.keys.extraction_candidate_key(
        state["book_id"], state["run_id"], ref["unit_id"],
    )
    if await storage.exists(candidate_key):
        artifact = json.loads(await storage.read_text(candidate_key))
        if (
            artifact.get("unit_id") != ref["unit_id"]
            or artifact.get("source_sha256") != ref["source_sha256"]
            or artifact.get("text_sha256") != ref["text_sha256"]
        ):
            raise ValueError("an immutable extraction-candidate artifact has conflicting source content")
        ref["learning_unit_ids"] = [
            candidate["learning_unit_id"]
            for candidate in artifact.get("candidates", [])
            if isinstance(candidate, dict) and isinstance(candidate.get("learning_unit_id"), str)
        ]
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "unit_extraction",
            status = "done",
            unit_id = ref["unit_id"],
        )
        return {"extracted_units": [ref]}

    learning_unit_key = domains.bd.distill.ai.keys.learning_unit_key(
        state["book_id"], state["run_id"], ref["unit_id"],
    )
    if await storage.exists(learning_unit_key):
        learning_unit = json.loads(await storage.read_text(learning_unit_key))
        if (
            learning_unit.get("unit_id") != ref["unit_id"]
            or learning_unit.get("source_sha256") != ref["source_sha256"]
            or learning_unit.get("text_sha256") != ref["text_sha256"]
        ):
            raise ValueError("an immutable learning-unit artifact has conflicting source content")
        candidate_artifact = _candidate_artifact_from_learning_unit(ref, learning_unit)
        await storage.write(
            candidate_key,
            domains.bd.distill.ai.domain.json_body(candidate_artifact),
            content_type = domains.bd.distill.ai.params.JSON_TYPE,
        )
        ref["learning_unit_ids"] = [
            candidate["learning_unit_id"] for candidate in candidate_artifact["candidates"]
            if isinstance(candidate.get("learning_unit_id"), str)
        ]
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "unit_extraction",
            status = "done",
            unit_id = ref["unit_id"],
        )
        return {"extracted_units": [ref]}

    await graphs.service.report(
        dependencies.progress_callback,
        kind = "activity",
        node = "extract_unit",
        current_task = f"Extracting lessons from page {ref['page_number']}",
    )
    prompt_payload = {
        "task": "Extract the important learning units from this source chunk.",
        "physical_page": ref["page_number"],
        "section_path": ref["section_path"],
        "source_chunk_id": ref["unit_id"],
        "markdown": source_text,
    }
    extractor_files = skills.service.files_for("distill_learning_extractor")
    extraction = None
    extraction_needs_review = False
    quote_repair_flags: list[str] = []
    response_attempts = agents.distill.learning_extractor.params.MAX_STRUCTURED_RESPONSE_ATTEMPTS
    for attempt in range(response_attempts):
        payload = dict(prompt_payload)
        if attempt:
            payload["response_retry"] = (
                "The previous response was not a valid LearningExtraction. Return the typed response now. "
                "Every lesson needs an exact quote from this page. If there are no supported lessons, "
                "return empty arrays and explain the omission."
            )
        try:
            result = await _invoke_learning_extractor(
                dependencies,
                state["run_id"],
                payload,
                extractor_files,
            )
        except Exception as error:
            if domains.settings.chat.domain.is_transient_provider_error(error):
                logger.warning(
                    "[bd-distill] transient provider failure stage=learning_extractor unit=%s page=%s kind=%s error=%s",
                    ref.get("unit_id"),
                    ref.get("page_number"),
                    domains.settings.chat.domain.classify_error(error),
                    type(error).__name__,
                )
            else:
                logger.exception(
                    "[bd-distill] extractor invocation failed for unit=%s page=%s",
                    ref["unit_id"],
                    ref["page_number"],
                )
            raise
        extraction = domains.bd.distill.ai.domain.learning_extraction_from_agent_result(result)
        if extraction is not None:
            break
        logger.warning(
            "[bd-distill] extractor returned no valid structured response for unit=%s attempt=%s/%s",
            ref["unit_id"],
            attempt + 1,
            response_attempts,
        )

    if extraction is None:
        logger.warning(
            "[bd-distill] preserving source chunk for review after repeated invalid structured responses "
            "unit=%s page=%s attempts=%s",
            ref["unit_id"],
            ref["page_number"],
            response_attempts,
        )
        extraction = domains.bd.distill.ai.schemas.LearningExtraction(lessons = [], omitted_material = [])
        extraction_needs_review = True

    if extraction.lessons:
        def has_supported_quote(lesson: domains.bd.distill.ai.schemas.ExtractedLesson) -> bool:
            return any(
                quote.page_number == ref["page_number"]
                and domains.bd.distill.ai.domain.quote_is_supported(quote.quote, source_text)
                for quote in lesson.evidence
            )

        unsupported_quote_lessons = [lesson for lesson in extraction.lessons if not has_supported_quote(lesson)]
        if unsupported_quote_lessons:
            await graphs.service.report_node_activity(
                dependencies.progress_callback,
                "extract_unit",
                f"Repairing source quotations on page {ref['page_number']}",
            )
            quote_repair_payload = {
                "task": "Repair exact evidence quotations for extracted source lessons.",
                "physical_page": ref["page_number"],
                "section_path": ref["section_path"],
                "source_chunk_id": ref["unit_id"],
                "markdown": source_text,
                "lesson_candidates": [lesson.model_dump(mode = "json") for lesson in unsupported_quote_lessons],
                "response_retry": (
                    "Return a LearningExtraction containing only corrected versions of the supplied candidates. "
                    "Keep each source-supported lesson's claim and explanation intact when possible. Replace each "
                    "invalid quote with an exact passage copied from markdown, on the stated physical page. Do not "
                    "invent or paraphrase quotations. Omit a candidate only if this page does not support it."
                ),
            }
            try:
                repair_result = await _invoke_learning_extractor(
                    dependencies,
                    state["run_id"],
                    quote_repair_payload,
                    extractor_files,
                )
                repaired_extraction = domains.bd.distill.ai.domain.learning_extraction_from_agent_result(repair_result)
                if repaired_extraction is not None:
                    supported_lessons = [lesson for lesson in extraction.lessons if has_supported_quote(lesson)]
                    seen_claims = {" ".join(lesson.claim.casefold().split()) for lesson in supported_lessons}
                    recovered_count = 0
                    for lesson in repaired_extraction.lessons:
                        if not has_supported_quote(lesson):
                            continue
                        normalized_claim = " ".join(lesson.claim.casefold().split())
                        if normalized_claim not in seen_claims and len(supported_lessons) < 8:
                            supported_lessons.append(lesson)
                            seen_claims.add(normalized_claim)
                            recovered_count += 1
                    extraction = extraction.model_copy(update = {"lessons": supported_lessons})
                    quote_repair_flags.append(
                        "Evidence quotations were corrected in one bounded source-grounded repair pass."
                        if recovered_count
                        else "The bounded quotation repair recovered no source-resolving replacement; unsupported quotes were excluded."
                    )
                else:
                    quote_repair_flags.append(
                        "An exact-quotation repair response could not be validated; unsupported quotations were excluded."
                    )
            except Exception as error:  # noqa: BLE001 — a failed optional quote repair retains valid lessons.
                quote_repair_flags.append(
                    f"An exact-quotation repair could not be completed ({type(error).__name__})."
                )
                logger.warning(
                    "[bd-distill] evidence quote repair failed unit=%s page=%s error_type=%s error=%s",
                    ref["unit_id"],
                    ref["page_number"],
                    type(error).__name__,
                    str(error)[:300],
                )

    candidates: list[dict[str, Any]] = []
    review_flags: list[str] = list(quote_repair_flags)
    if extraction_needs_review:
        review_flags.append(
            f"Learning extraction could not validate a model response for source chunk {ref['unit_id']} "
            f"on page {ref['page_number']}; review the original page before relying on this section."
        )
    for lesson in extraction.lessons:
        evidence = [
            quote.model_dump(mode = "json")
            for quote in lesson.evidence
            if quote.page_number == ref["page_number"] and domains.bd.distill.ai.domain.quote_is_supported(
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
    for index, candidate in enumerate(candidates):
        candidate["learning_unit_id"] = domains.bd.distill.ai.domain.stable_id(
            ref["unit_id"], str(index), candidate["claim"],
        )

    candidate_artifact = {
        "unit_id": ref["unit_id"],
        "page_number": ref["page_number"],
        "processed_page_number": input_record.get("processed_page_number"),
        "source_key": input_record["source_key"],
        "section_path": ref["section_path"],
        "source_sha256": ref["source_sha256"],
        "text_sha256": ref["text_sha256"],
        "source_char_range": [ref["char_start"], ref["char_end"]],
        "extraction_status": "needs_review" if extraction_needs_review else "complete",
        "candidates": candidates,
        "omitted_material": extraction.omitted_material,
        "review_flags": sorted(set(review_flags)),
    }
    await storage.write(
        candidate_key,
        domains.bd.distill.ai.domain.json_body(candidate_artifact),
        content_type = domains.bd.distill.ai.params.JSON_TYPE,
    )
    ref["learning_unit_ids"] = [candidate["learning_unit_id"] for candidate in candidates]
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "unit_extraction",
        status = "done",
        unit_id = ref["unit_id"],
    )
    return {"extracted_units": [ref]}
