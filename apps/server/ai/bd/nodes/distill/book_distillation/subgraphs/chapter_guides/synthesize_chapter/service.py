"""Synthesize source-grounded chapter learning guides."""
from __future__ import annotations
import domains
from . import errors
from ....... import graphs, prompts, skills

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _synthesize_chapter_with_agent(
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    prompt: str,
    *,
    counter_node_id: str = "chapter_synthesizer",
    response_attempts: int | None = None,
) -> domains.bd.distill.ai.schemas.ChapterSynthesis:
    """Run the chapter DeepAgent and repair schema output at most once."""
    controls = dependencies.controls
    system_prompt = prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT
    user_request = prompt.removeprefix(system_prompt).strip()
    response_attempts = (
        response_attempts
        if response_attempts is not None
        else domains.bd.distill.ai.params.CHAPTER_STRUCTURED_OUTPUT_ATTEMPTS
    )
    last_error: Exception | None = None

    skill_files = skills.service.files_for("distill_chapter_synthesizer")

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
                        pool_error = domains.settings.chat.domain.unsettled_pool_error(
                            settled,
                            max_cooling_frac = domains.settings.chat.params.SETTLE_MAX_COOLING_FRAC,
                        )
                        if pool_error is not None:
                            raise pool_error
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
                            "files": skill_files,
                        }, config = {
                            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                        }),
                        attempts = domains.bd.distill.ai.params.CHAPTER_SYNTHESIS_PROVIDER_ATTEMPTS,
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
            issues = (
                str(last_error).split(": ", maxsplit = 1)[-1]
                if last_error is not None
                else "no_valid_agent_response"
            )
            payload["response_retry"] = (
                "The previous answer was empty or did not validate as ChapterSynthesis. Return the complete "
                "structured response again, correct the listed schema issues, preserve source-grounded content, "
                "and cite only supplied learning-unit IDs."
            )
            payload["validation_issue"] = issues[:500]
        result = await run_agent_call(payload)
        synthesis = domains.bd.distill.ai.domain.chapter_synthesis_from_agent_result(result)
        if synthesis is not None:
            return synthesis
        issues = domains.bd.distill.ai.domain.chapter_synthesis_validation_issues(result)
        last_error = errors.ChapterSynthesisOutputError(
            "chapter DeepAgent returned invalid ChapterSynthesis: " + ", ".join(issues),
        )
        logger.warning(
            "[bd-distill] chapter DeepAgent returned invalid structured output "
            "run=%s chapter=%s attempt=%s/%s error_type=%s",
            run_id,
            chapter_id,
            attempt + 1,
            response_attempts,
            type(last_error).__name__ if last_error else "InvalidAgentResponse",
        )
    if last_error is not None:
        raise errors.ChapterSynthesisOutputError(
            f"chapter synthesizer returned invalid structured output after {response_attempts} attempt(s): "
            f"{type(last_error).__name__}"
        ) from last_error
    raise errors.ChapterSynthesisOutputError(
        f"chapter synthesizer returned no valid ChapterSynthesis after {response_attempts} attempt(s)"
    )


def _ensure_chapter_citations(
    chapter_id: str,
    synthesis: domains.bd.distill.ai.schemas.ChapterSynthesis,
    allowed_ids: set[str],
) -> tuple[domains.bd.distill.ai.schemas.ChapterSynthesis, list[str]]:
    """Deterministically retain only source-resolvable prose citations."""
    issues = domains.bd.distill.ai.synthesize.domain.chapter_citation_issues(synthesis, allowed_ids)
    if not issues:
        return synthesis, []

    repaired, review_flags = domains.bd.distill.ai.synthesize.domain.repair_chapter_citations(synthesis, allowed_ids)
    logger.warning(
        "[bd-distill] normalized unresolved chapter citations chapter=%s repair=%s issues=%s flags=%s",
        chapter_id,
        "deterministic",
        issues,
        review_flags,
    )
    return repaired, review_flags


async def revise_chapter_for_coverage(
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
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
        }, ensure_ascii = False, separators = (",", ":"))
    )
    revised = await _synthesize_chapter_with_agent(
        dependencies,
        run_id,
        chapter_id,
        prompt,
        counter_node_id = "chapter_coverage_corrector",
        response_attempts = domains.bd.distill.ai.params.COVERAGE_CORRECTOR_RESPONSE_ATTEMPTS,
    )
    allowed_ids = {unit["learning_unit_id"] for unit in units}
    revised, citation_flags = _ensure_chapter_citations(
        chapter_id,
        revised,
        allowed_ids,
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
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
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
        + json.dumps(
            {"section": chapter_input, "learning_units": units},
            ensure_ascii = False,
            separators = (",", ":"),
        )
    )
    synthesis_review_flags: list[str] = []
    try:
        synthesis = await _synthesize_chapter_with_agent(
            dependencies,
            state["run_id"],
            ref["chapter_id"],
            prompt,
        )
    except errors.ChapterSynthesisOutputError as error:
        synthesis = domains.bd.distill.ai.synthesize.domain.fallback_chapter_synthesis(units)
        synthesis_review_flags.append(
            "The structured chapter response was invalid after one correction attempt. "
            "This guide uses the source-cited "
            "learning units; learning objectives and practice items were omitted and this section needs review."
        )
        logger.warning(
            "[bd-distill] using verified-source chapter fallback run=%s chapter=%s issue=%s",
            state["run_id"],
            ref["chapter_id"],
            type(error).__name__,
        )
    allowed_ids = set(ref["unit_ids"])
    citation_review_flags: list[str] = []
    synthesis, citation_flags = _ensure_chapter_citations(
        ref["chapter_id"],
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


