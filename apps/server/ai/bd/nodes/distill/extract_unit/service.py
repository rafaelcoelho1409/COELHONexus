"""Persist bounded source chunks and extract cited learning units from them."""
from __future__ import annotations
import domains
from .... import agents, graphs, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


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
    # Load role files before the recoverable model-response block. Missing packaged skills are
    # deployment errors and must fail this graph node instead of being saved as empty extraction.
    extractor_files = skills.service.files_for("distill_learning_extractor")
    extraction = None
    extraction_needs_review = False
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
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = state["run_id"], node_id = "learning_extractor", stage = "distill",
            ):
                result = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: dependencies.learning_extractor.ainvoke({
                        "messages": [{
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii = False),
                        }],
                        "files": extractor_files,
                    }, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }),
                    attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )
        except Exception as error:  # noqa: BLE001 — unexpected invocation failures must fail this graph node.
            if domains.settings.chat.domain.is_transient_provider_error(error):
                logger.warning(
                    "[bd-distill] transient provider failure stage=agent unit=%s page=%s kind=%s error=%s",
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
        extraction = domains.bd.distill.domain.learning_extraction_from_agent_result(result)
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
        extraction = domains.bd.distill.schemas.LearningExtraction(lessons = [], omitted_material = [])
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
        verifier_files = skills.service.files_for("distill_evidence_verifier")
        try:
            payload = {"page_number": ref["page_number"], "lessons": candidates}
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = state["run_id"], node_id = "evidence_verifier", stage = "distill",
            ):
                result = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: dependencies.evidence_verifier.ainvoke({
                        "messages": [{
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii = False),
                        }],
                        "files": verifier_files,
                    }, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }),
                    attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )
            review = domains.bd.distill.domain.structured_model_from_agent_result(
                result,
                domains.bd.distill.schemas.EvidenceReview,
            )
            if not isinstance(review, domains.bd.distill.schemas.EvidenceReview):
                raise ValueError("the evidence verifier returned no valid EvidenceReview")
            verdicts = {item.lesson_index: item for item in review.verdicts}
        except Exception as error:  # noqa: BLE001 — unavailable semantic review retains a review-marked citation.
            verdicts = {}
            if domains.settings.chat.domain.is_transient_provider_error(error):
                logger.warning(
                    "[bd-distill] transient provider failure stage=evidence_verifier unit=%s page=%s kind=%s error=%s",
                    ref.get("unit_id"),
                    ref.get("page_number"),
                    domains.settings.chat.domain.classify_error(error),
                    type(error).__name__,
                )
            review_flags.append(
                f"Evidence review was unavailable ({type(error).__name__}); supported quotes require human review."
            )
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
        "processed_page_number": input_record.get("processed_page_number"),
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
    await storage.write(
        output_key,
        domains.bd.distill.domain.json_body(artifact),
        content_type = domains.bd.distill.params.JSON_TYPE,
    )
    ref["learning_unit_ids"] = [item["learning_unit_id"] for item in persisted_lessons]
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "unit",
        status = "done",
        unit_id = ref["unit_id"],
        lesson_count = len(persisted_lessons),
    )
    return {"completed_units": [ref]}
