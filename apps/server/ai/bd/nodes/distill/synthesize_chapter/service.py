"""Synthesize source-grounded chapter learning guides."""
from __future__ import annotations
import domains
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
) -> domains.bd.distill.schemas.ChapterSynthesis:
    """Use the chapter DeepAgent and retry only with typed-output repair instructions."""
    controls = dependencies.controls
    system_prompt = prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT
    user_request = prompt.removeprefix(system_prompt).strip()
    response_attempts = domains.bd.distill.params.CHAPTER_AGENT_RESPONSE_ATTEMPTS
    last_error: Exception | None = None

    async def run_agent_call(payload: dict[str, Any]) -> Any:
        async with controls.chapter_synthesis_semaphore:
            async with controls.chapter_settle_lock:
                if not controls.chapter_pool_settled:
                    domains.settings.chat.service.refresh_endpoint()
                    endpoint = domains.settings.chat.service.ENDPOINT
                    if domains.settings.chat.domain.rotator_health_url(endpoint.base_url):
                        settled = await domains.settings.chat.service.wait_for_rotator_settle(
                            domains.bd.distill.params.CHAPTER_SYNTHESIS_SETTLE_CAP_S,
                        )
                        logger.info("[bd-distill] chapter synthesis Rotator settle: %s", settled)
                    controls.chapter_pool_settled = True
            try:
                with domains.settings.llm_counter.service.llm_context(
                    ns = "bd", run_id = run_id, node_id = "chapter_synthesizer", stage = "distill",
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
                        attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
                        wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
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
                "Keep only supplied learning-unit IDs and include the required overview, lessons, and open_questions. "
                "Leave visual collections empty; the visual specialist handles them."
            )
            if last_error is not None:
                payload["validation_issue"] = f"{type(last_error).__name__}: {str(last_error)[:900]}"
        try:
            result = await run_agent_call(payload)
            synthesis = domains.bd.distill.domain.chapter_synthesis_from_agent_result(result)
            if synthesis is not None:
                return synthesis
            last_error = ValueError("chapter DeepAgent returned no valid ChapterSynthesis")
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
    raise ValueError("chapter synthesizer returned no valid ChapterSynthesis after DeepAgent retries") from last_error


async def _ensure_chapter_citations(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    prompt: str,
    synthesis: domains.bd.distill.schemas.ChapterSynthesis,
    allowed_ids: set[str],
) -> tuple[domains.bd.distill.schemas.ChapterSynthesis, list[str]]:
    """Correct model-invented prose citations, then retain only source-resolvable output."""
    issues = domains.bd.distill.synthesize.domain.chapter_citation_issues(synthesis, allowed_ids)
    for _ in range(domains.bd.distill.params.CHAPTER_CITATION_REPAIR_ATTEMPTS):
        if not issues:
            break
        repair_prompt = (
            prompt
            + "\n\nCitation correction required. The previous response used learning-unit IDs "
            + "that are not present in this section's input. Return a complete corrected chapter "
            + "guide. Preserve supported prose, but use only exact IDs from the supplied section; "
            + "never invent, infer, or copy an ID from another section. Every overview, lesson, "
            + "and open question must cite at least one allowed ID.\nAllowed IDs: "
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
            )
        except Exception as error:  # noqa: BLE001 — sanitize citations when the optional repair call fails.
            logger.warning(
                "[bd-distill] chapter citation correction call failed; using deterministic repair "
                "chapter=%s error_type=%s",
                chapter_id,
                type(error).__name__,
            )
            break
        issues = domains.bd.distill.synthesize.domain.chapter_citation_issues(synthesis, allowed_ids)

    if not issues:
        return synthesis, []

    repaired, review_flags = domains.bd.distill.synthesize.domain.repair_chapter_citations(synthesis, allowed_ids)
    logger.warning(
        "[bd-distill] normalized unresolved chapter citations chapter=%s repair_attempts=%s issues=%s flags=%s",
        chapter_id,
        domains.bd.distill.params.CHAPTER_CITATION_REPAIR_ATTEMPTS,
        issues,
        review_flags,
    )
    return repaired, review_flags


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
        units = await domains.bd.distill.synthesize.service.read_learning_units(
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
    synthesis = await _synthesize_chapter_with_llm(
        dependencies,
        state["run_id"],
        ref["chapter_id"],
        prompt,
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
        "visual_review_flags": [],
        "visuals_status": "pending",
    }
    await storage.write(
        ref["artifact_key"],
        domains.bd.distill.domain.json_body(artifact),
        content_type = domains.bd.distill.params.JSON_TYPE,
    )
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "chapter_guide",
        status = "done",
        chapter_id = ref["chapter_id"],
    )
    return {"synthesized_chapters": [ref]}


