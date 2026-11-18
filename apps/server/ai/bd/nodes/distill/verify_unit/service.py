"""Assess whether exact source passages support extracted lesson candidates."""
from __future__ import annotations
import domains
from .... import graphs, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def verify_unit(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    ref = state["unit_ref"]
    storage = dependencies.storage
    output_key = domains.bd.distill.keys.learning_unit_key(state["book_id"], state["run_id"], ref["unit_id"])
    if await storage.exists(output_key):
        artifact = json.loads(await storage.read_text(output_key))
        if (
            artifact.get("unit_id") != ref["unit_id"]
            or artifact.get("source_sha256") != ref["source_sha256"]
            or artifact.get("text_sha256") != ref["text_sha256"]
        ):
            raise ValueError("an immutable learning-unit artifact has conflicting source content")
        ref["learning_unit_ids"] = [item["learning_unit_id"] for item in artifact.get("lessons", [])]
        ref["existing_artifact"] = True
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "unit_verification",
            status = "done",
            unit_id = ref["unit_id"],
        )
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "unit",
            status = "done",
            unit_id = ref["unit_id"],
            lesson_count = len(ref["learning_unit_ids"]),
        )
        return {"completed_units": [ref]}

    candidate_key = domains.bd.distill.keys.extraction_candidate_key(
        state["book_id"], state["run_id"], ref["unit_id"],
    )
    candidate_artifact = json.loads(await storage.read_text(candidate_key))
    if (
        candidate_artifact.get("unit_id") != ref["unit_id"]
        or candidate_artifact.get("source_sha256") != ref["source_sha256"]
        or candidate_artifact.get("text_sha256") != ref["text_sha256"]
    ):
        raise ValueError("an immutable extraction-candidate artifact has conflicting source content")
    candidates = candidate_artifact.get("candidates", [])
    if not isinstance(candidates, list) or any(not isinstance(candidate, dict) for candidate in candidates):
        raise ValueError("the persisted extraction-candidate artifact has an invalid candidate list")
    verdicts: dict[int, Any] = {}
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "activity",
        node = "verify_unit",
        current_task = f"Checking lesson evidence on page {ref['page_number']}",
    )

    if candidates:
        verifier_files = skills.service.files_for("distill_evidence_verifier")
        try:
            payload = {
                "page_number": ref["page_number"],
                "lessons": [
                    {key: value for key, value in candidate.items() if key != "learning_unit_id"}
                    for candidate in candidates
                ],
            }
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
        except Exception as error:  # noqa: BLE001 — non-transient review failures leave lessons for human review.
            if domains.settings.chat.domain.is_transient_provider_error(error):
                logger.warning(
                    "[bd-distill] transient provider failure stage=evidence_verifier unit=%s page=%s kind=%s error=%s",
                    ref.get("unit_id"),
                    ref.get("page_number"),
                    domains.settings.chat.domain.classify_error(error),
                    type(error).__name__,
                )
                raise
            logger.warning(
                "[bd-distill] evidence review unavailable; retaining cited lessons for review "
                "unit=%s error_type=%s",
                ref["unit_id"],
                type(error).__name__,
            )
            review_error = error
        else:
            review_error = None
    else:
        review_error = None

    review_flags = list(candidate_artifact.get("review_flags", []))
    if review_error is not None:
        review_flags.append(
            f"Evidence review was unavailable ({type(review_error).__name__}); supported quotes require human review."
        )

    persisted_lessons = []
    for index, candidate in enumerate(candidates):
        verdict = verdicts.get(index)
        if verdict is not None and not verdict.supported:
            review_flags.append("An evidence reviewer rejected a proposed lesson as unsupported; it was excluded.")
            continue
        lesson_id = candidate.get("learning_unit_id")
        if not isinstance(lesson_id, str):
            lesson_id = domains.bd.distill.domain.stable_id(ref["unit_id"], str(index), candidate["claim"])
        persisted_lessons.append({
            "learning_unit_id": lesson_id,
            **{key: value for key, value in candidate.items() if key != "learning_unit_id"},
            "verification_status": "verified" if verdict is not None else "unverified",
            "verification_note": verdict.reason if verdict is not None else None,
        })
        if verdict is None:
            review_flags.append("A lesson has a source quote but no independent semantic evidence verdict.")

    artifact = {
        key: candidate_artifact[key]
        for key in (
            "unit_id",
            "page_number",
            "processed_page_number",
            "source_key",
            "section_path",
            "source_sha256",
            "text_sha256",
            "source_char_range",
            "extraction_status",
            "omitted_material",
        )
        if key in candidate_artifact
    }
    artifact.update({
        "lessons": persisted_lessons,
        "review_flags": sorted(set(review_flags)),
    })
    await storage.write(
        output_key,
        domains.bd.distill.domain.json_body(artifact),
        content_type = domains.bd.distill.params.JSON_TYPE,
    )
    ref["learning_unit_ids"] = [item["learning_unit_id"] for item in persisted_lessons]
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "unit_verification",
        status = "done",
        unit_id = ref["unit_id"],
    )
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "unit",
        status = "done",
        unit_id = ref["unit_id"],
        lesson_count = len(persisted_lessons),
    )
    return {"completed_units": [ref]}
