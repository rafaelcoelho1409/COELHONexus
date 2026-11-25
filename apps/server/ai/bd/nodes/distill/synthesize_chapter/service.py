"""Synthesize source-grounded chapter learning guides."""
from __future__ import annotations
import domains
from . import errors
from .... import graphs, prompts, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def _synthesize_chapter_with_llm(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    prompt: str,
    *,
    counter_node_id: str = "chapter_synthesizer",
    response_attempts: int | None = None,
    provider_attempts: int | None = None,
) -> domains.bd.distill.ai.schemas.ChapterSynthesis:
    """Use the chapter DeepAgent and retry only with typed-output repair instructions."""
    controls = dependencies.controls
    system_prompt = prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT
    user_request = prompt.removeprefix(system_prompt).strip()
    response_attempts = response_attempts or domains.bd.distill.ai.params.CHAPTER_AGENT_RESPONSE_ATTEMPTS
    provider_attempts = provider_attempts or domains.bd.distill.ai.params.AGENT_CALL_ATTEMPTS
    last_error: Exception | None = None

    async def run_agent_call(payload: dict[str, Any]) -> Any:
        async with controls.chapter_synthesis_semaphore:
            async with controls.chapter_settle_lock:
                if not controls.chapter_pool_settled:
                    domains.settings.chat.service.refresh_endpoint()
                    endpoint = domains.settings.chat.service.ENDPOINT
                    if domains.settings.chat.domain.rotator_health_url(endpoint.base_url):
                        settled = await domains.settings.chat.service.wait_for_rotator_settle(
                            domains.bd.distill.ai.params.CHAPTER_SYNTHESIS_SETTLE_CAP_S,
                        )
                        logger.info("[bd-distill] chapter synthesis Rotator settle: %s", settled)
                    controls.chapter_pool_settled = True
            try:
                with domains.settings.llm_counter.service.llm_context(
                    ns = "bd", run_id = run_id, node_id = counter_node_id, stage = "distill",
                ):
                    return await domains.settings.chat.service.resilient_ainvoke(
                        lambda: dependencies.chapter_synthesizer.ainvoke({
                            "messages": [{
                                "role": "user",
                                "content": json.dumps(payload, ensure_ascii = False),
                            }],
                            "files": skills.service.files_for("distill_chapter_synthesizer"),
                        }, config = {
                            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                        }),
                        attempts = provider_attempts,
                        wait_base_s = domains.bd.distill.ai.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                    )
            except Exception as error:
                if domains.settings.chat.domain.is_transient_provider_error(error):
                    async with controls.chapter_settle_lock:
                        controls.chapter_pool_settled = False
                raise

    for attempt in range(response_attempts):
        payload: dict[str, Any] = {"chapter_request": user_request}
        if attempt:
            payload["response_retry"] = (
                "The previous answer was empty or did not validate as ChapterSynthesis. Return the full typed schema. "
                "Correct every schema path named in validation_issue and return one JSON object, not prose or a wrapper. "
                "Keep only supplied learning-unit IDs and include overview, lessons, open_questions, learning_objectives, "
                "and practice_items. Practice objective_ids must reference objectives in the same response. "
                "Leave visual collections empty; the visual specialist handles them."
            )
            if last_error is not None:
                issue = (
                    str(last_error)
                    if isinstance(last_error, errors.ChapterSynthesisOutputError)
                    else type(last_error).__name__
                )
                payload["validation_issue"] = f"{type(last_error).__name__}: {issue[:500]}"
        try:
            result = await run_agent_call(payload)
            synthesis = domains.bd.distill.ai.domain.chapter_synthesis_from_agent_result(result)
            if synthesis is not None:
                return synthesis
            issues = domains.bd.distill.ai.domain.chapter_synthesis_validation_issues(result)
            last_error = errors.ChapterSynthesisOutputError(
                "chapter DeepAgent returned invalid ChapterSynthesis: " + ", ".join(issues),
            )
        except Exception as error:  # noqa: BLE001 — graph retry handles provider failures; schema retries stay local.
            last_error = error
            if domains.settings.chat.domain.is_transient_provider_error(error):
                raise
        logger.warning(
            "[bd-distill] chapter DeepAgent returned invalid structured output "
            "run=%s chapter=%s attempt=%s/%s error_type=%s",
            run_id,
            chapter_id,
            attempt + 1,
            response_attempts,
            type(last_error).__name__ if last_error else "InvalidAgentResponse",
        )
    if last_error is not None and not isinstance(last_error, errors.ChapterSynthesisOutputError):
        raise last_error
    issue = str(last_error) if last_error is not None else "unknown schema issue"
    raise errors.ChapterSynthesisOutputError(
        f"chapter synthesizer returned no valid ChapterSynthesis after {response_attempts} attempts: {issue}",
    ) from last_error


async def _ensure_chapter_citations(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    prompt: str,
    synthesis: domains.bd.distill.ai.schemas.ChapterSynthesis,
    allowed_ids: set[str],
    *,
    counter_node_id: str = "chapter_synthesizer",
    response_attempts: int | None = None,
    provider_attempts: int | None = None,
) -> tuple[domains.bd.distill.ai.schemas.ChapterSynthesis, list[str]]:
    """Correct model-invented prose citations, then retain only source-resolvable output."""
    issues = domains.bd.distill.ai.synthesize.domain.chapter_citation_issues(synthesis, allowed_ids)
    for _ in range(domains.bd.distill.ai.params.CHAPTER_CITATION_REPAIR_ATTEMPTS):
        if not issues:
            break
        repair_prompt = (
            prompt
            + "\n\nCitation correction required. The previous response used learning-unit IDs "
            + "that are not present in this section's input. Return a complete corrected chapter "
            + "guide. Preserve supported prose, but use only exact IDs from the supplied section; "
            + "never invent, infer, or copy an ID from another section. Every overview, lesson, "
            + "open question, learning objective, and practice item must cite at least one allowed ID. "
            + "Practice objective_ids must refer to objectives in the same response.\nAllowed IDs: "
            + json.dumps(sorted(allowed_ids), ensure_ascii = False)
            + "\nValidation findings: "
            + json.dumps(issues, ensure_ascii = False)
            + "\nPrevious response: "
            + json.dumps(synthesis.model_dump(mode = "json"), ensure_ascii = False)
        )
        try:
            synthesis = await _synthesize_chapter_with_llm(
                dependencies,
                run_id,
                chapter_id,
                repair_prompt,
                counter_node_id = counter_node_id,
                response_attempts = response_attempts,
                provider_attempts = provider_attempts,
            )
        except Exception as error:  # noqa: BLE001 — sanitize citations when the optional repair call fails.
            logger.warning(
                "[bd-distill] chapter citation correction call failed; using deterministic repair "
                "chapter=%s error_type=%s",
                chapter_id,
                type(error).__name__,
            )
            break
        issues = domains.bd.distill.ai.synthesize.domain.chapter_citation_issues(synthesis, allowed_ids)

    if not issues:
        return synthesis, []

    repaired, review_flags = domains.bd.distill.ai.synthesize.domain.repair_chapter_citations(synthesis, allowed_ids)
    logger.warning(
        "[bd-distill] normalized unresolved chapter citations chapter=%s repair_attempts=%s issues=%s flags=%s",
        chapter_id,
        domains.bd.distill.ai.params.CHAPTER_CITATION_REPAIR_ATTEMPTS,
        issues,
        review_flags,
    )
    return repaired, review_flags


async def revise_chapter_for_coverage(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    chapter_input: dict[str, Any],
    synthesis: domains.bd.distill.ai.schemas.ChapterSynthesis,
    units: list[dict[str, Any]],
    findings: list[dict[str, str]],
) -> tuple[domains.bd.distill.ai.schemas.ChapterSynthesis, list[str]]:
    """Apply one bounded reviewer correction using only the chapter's verified evidence."""
    prompt = (
        prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT
        + "\n\nCorrect this chapter guide using the editorial findings. Preserve accurate lessons and "
        + "learning detail. Address only findings supported by the supplied verified learning units; "
        + "do not invent facts, quotations, or learning-unit IDs. If a finding cannot be corrected from "
        + "the supplied evidence, leave the relevant content unchanged. Return the full ChapterSynthesis.\n"
        + json.dumps({
            "section": chapter_input,
            "verified_learning_units": units,
            "current_chapter_guide": synthesis.model_dump(mode = "json"),
            "editorial_findings": findings,
        }, ensure_ascii = False)
    )
    revised = await _synthesize_chapter_with_llm(
        dependencies,
        run_id,
        chapter_id,
        prompt,
        counter_node_id = "chapter_coverage_corrector",
        response_attempts = domains.bd.distill.ai.params.COVERAGE_CORRECTOR_RESPONSE_ATTEMPTS,
        provider_attempts = domains.bd.distill.ai.params.COVERAGE_CORRECTOR_PROVIDER_ATTEMPTS,
    )
    allowed_ids = {unit["learning_unit_id"] for unit in units}
    revised, citation_flags = await _ensure_chapter_citations(
        dependencies,
        run_id,
        chapter_id,
        prompt,
        revised,
        allowed_ids,
        counter_node_id = "chapter_coverage_corrector",
        response_attempts = domains.bd.distill.ai.params.COVERAGE_CORRECTOR_RESPONSE_ATTEMPTS,
        provider_attempts = domains.bd.distill.ai.params.COVERAGE_CORRECTOR_PROVIDER_ATTEMPTS,
    )
    revised = revised.model_copy(update = {
        "diagrams": [],
        "equations": [],
        "charts": [],
        "timelines": [],
        "maps": [],
    })
    return revised, citation_flags


async def synthesize_chapter(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
    learning_units_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    storage = dependencies.storage
    ref = state["chapter_ref"]
    if await storage.exists(ref["artifact_key"]):
        artifact = json.loads(await storage.read_text(ref["artifact_key"]))
        if artifact.get("unit_ids") != ref["unit_ids"]:
            raise ValueError("an immutable chapter report key has conflicting learning-unit IDs")
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "chapter_guide",
            status = "done",
            chapter_id = ref["chapter_id"],
        )
        return {"synthesized_chapters": [ref]}

    chapter_input = json.loads(await storage.read_text(ref["input_key"]))
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "activity",
        node = "synthesize_chapter",
        current_task = f"Writing guide: {chapter_input['title']}",
    )
    if learning_units_by_id is None:
        units = await domains.bd.distill.ai.synthesize.service.read_learning_units(
            storage,
            state["book_id"],
            state["run_id"],
            ref["unit_ids"],
            ref.get("unit_sources") or chapter_input.get("unit_sources"),
        )
    else:
        missing_ids = set(ref["unit_ids"]) - learning_units_by_id.keys()
        if missing_ids:
            raise ValueError("chapter synthesis did not receive all of its persisted learning units")
        units = [learning_units_by_id[unit_id] for unit_id in ref["unit_ids"]]
    prompt = (
        prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT
        + "\n\nWrite this section's learning guide. "
        + "Every generated lesson, overview, question, diagram node/edge, and equation "
        + "must use only the available learning_unit_ids.\n"
        + json.dumps({"section": chapter_input, "learning_units": units}, ensure_ascii = False)
    )
    synthesis_review_flags: list[str] = []
    try:
        synthesis = await _synthesize_chapter_with_llm(
            dependencies,
            state["run_id"],
            ref["chapter_id"],
            prompt,
        )
    except errors.ChapterSynthesisOutputError as error:
        synthesis = domains.bd.distill.ai.synthesize.domain.fallback_chapter_synthesis(units)
        synthesis_review_flags.append(
            "The structured chapter response was invalid after repair attempts. This guide uses the source-cited "
            "learning units; learning objectives and practice items were omitted and this section needs review."
        )
        logger.warning(
            "[bd-distill] using verified-source chapter fallback run=%s chapter=%s issue=%s",
            state["run_id"],
            ref["chapter_id"],
            str(error)[:500],
        )
    allowed_ids = set(ref["unit_ids"])
    citation_review_flags: list[str] = []
    synthesis, citation_flags = await _ensure_chapter_citations(
        dependencies,
        state["run_id"],
        ref["chapter_id"],
        prompt,
        synthesis,
        allowed_ids,
    )
    citation_review_flags.extend(citation_flags)

    synthesis = synthesis.model_copy(update = {
        "diagrams": [],
        "equations": [],
        "charts": [],
        "timelines": [],
        "maps": [],
    })
    artifact = {
        "chapter_id": ref["chapter_id"],
        "title": chapter_input["title"],
        "section_path": ref["section_path"],
        "unit_ids": ref["unit_ids"],
        "synthesis": synthesis.model_dump(mode = "json"),
        "citation_review_flags": list(dict.fromkeys(citation_review_flags)),
        "synthesis_review_flags": list(dict.fromkeys(synthesis_review_flags)),
        "visual_review_flags": [],
        "visuals_status": "pending",
    }
    await storage.write(
        ref["artifact_key"],
        domains.bd.distill.ai.domain.json_body(artifact),
        content_type = domains.bd.distill.ai.params.JSON_TYPE,
    )
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "chapter_guide",
        status = "done",
        chapter_id = ref["chapter_id"],
    )
    return {"synthesized_chapters": [ref]}


