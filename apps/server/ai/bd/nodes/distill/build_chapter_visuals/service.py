"""Build and validate visual artifacts for saved chapter guides."""
from __future__ import annotations
import domains
from .... import graphs, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def _build_visual_artifacts(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    payload: dict[str, Any],
) -> domains.bd.distill.schemas.VisualArtifactOutput:
    """Build separately typed visuals, retrying malformed results without weakening citations."""
    attempts = domains.bd.distill.params.CHAPTER_VISUAL_REPAIR_ATTEMPTS + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = dict(payload)
        if attempt:
            request["response_retry"] = (
                "The previous visual artifacts failed evidence validation. Correct the supplied items, "
                "or omit unsupported visuals. Every visual component must use exact evidence quotes and "
                "allowed learning-unit IDs from the request."
            )
            request["validation_issue"] = str(payload.get("validation_findings") or "invalid evidence")[:1200]
        try:
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = run_id, node_id = "visual_artifact_builder", stage = "distill",
            ):
                result = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: dependencies.visual_artifact_builder.ainvoke({
                        "messages": [{
                            "role": "user",
                            "content": json.dumps(request, ensure_ascii = False),
                        }],
                        "files": skills.service.files_for("distill_visual_artifact_builder"),
                    }, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }),
                    attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )
            visuals = domains.bd.distill.domain.structured_model_from_agent_result(
                result,
                domains.bd.distill.schemas.VisualArtifactOutput,
            )
            if isinstance(visuals, domains.bd.distill.schemas.VisualArtifactOutput):
                return visuals
            last_error = ValueError("visual-artifact DeepAgent returned no valid VisualArtifactOutput")
        except Exception as error:  # noqa: BLE001 — optional visuals cannot invalidate an otherwise sound guide.
            last_error = error
            if domains.settings.chat.domain.is_transient_provider_error(error):
                raise
        logger.warning(
            "[bd-distill] visual-artifact DeepAgent returned invalid output run=%s chapter=%s "
            "attempt=%s/%s error_type=%s",
            run_id,
            chapter_id,
            attempt + 1,
            attempts,
            type(last_error).__name__ if last_error else "InvalidAgentResponse",
        )
    raise ValueError("visual-artifact builder returned no valid structured output") from last_error


async def build_chapter_visuals(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate visuals after the readable chapter guide is durably saved."""
    storage = dependencies.storage
    ref = state["chapter_ref"]
    artifact = json.loads(await storage.read_text(ref["artifact_key"]))
    if artifact.get("unit_ids") != ref["unit_ids"]:
        raise ValueError("an immutable chapter report key has conflicting learning-unit IDs")
    # Artifacts from the previous combined stage have no status field, but their visuals are final.
    if artifact.get("visuals_status") in {None, "complete", "unavailable"}:
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "chapter_visuals",
            status = "done",
            chapter_id = ref["chapter_id"],
        )
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "chapter",
            status = "done",
            chapter_id = ref["chapter_id"],
        )
        return {"completed_chapters": [ref]}
    if artifact.get("visuals_status") != "pending":
        raise ValueError("chapter visual artifact has an unknown generation status")

    chapter_input = json.loads(await storage.read_text(ref["input_key"]))
    units = await domains.bd.distill.synthesize.service.read_learning_units(
        storage,
        state["book_id"],
        state["run_id"],
        ref["unit_ids"],
        ref.get("unit_sources") or chapter_input.get("unit_sources"),
    )
    synthesis = domains.bd.distill.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
    allowed_ids = set(ref["unit_ids"])
    visual_request: dict[str, Any] = {
        "section": chapter_input,
        "chapter_synthesis": {
            "overview": synthesis.overview,
            "lessons": [item.model_dump(mode = "json") for item in synthesis.lessons],
            "open_questions": [item.model_dump(mode = "json") for item in synthesis.open_questions],
        },
        "learning_units": units,
        "allowed_learning_unit_ids": sorted(allowed_ids),
    }
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "activity",
        node = "build_chapter_visuals",
        current_task = f"Building visuals: {chapter_input['title']}",
    )
    visual_review_flags: list[str] = []
    visual_generation_unavailable = False
    try:
        visual_output = await _build_visual_artifacts(
            dependencies,
            state["run_id"],
            ref["chapter_id"],
            visual_request,
        )
        synthesis = domains.bd.distill.synthesize.domain.merge_visual_artifacts(synthesis, visual_output)
    except Exception as error:  # noqa: BLE001 — optional visuals cannot invalidate the saved chapter guide.
        visual_generation_unavailable = True
        visual_review_flags.append(
            f"Visual generation was unavailable ({type(error).__name__}); the chapter guide remains text-only."
        )
        logger.warning(
            "[bd-distill] optional visual generation failed; retaining text-only chapter "
            "chapter=%s error_type=%s",
            ref["chapter_id"],
            type(error).__name__,
        )

    visual_issues = domains.bd.distill.synthesize.domain.visual_evidence_issues(synthesis, units)
    for _ in range(domains.bd.distill.params.CHAPTER_VISUAL_REPAIR_ATTEMPTS):
        if not visual_issues:
            break
        visual_request["validation_findings"] = [
            {"kind": kind, "title": title, "reason": reason}
            for kind, _, title, reason in visual_issues
        ]
        visual_request["previous_artifacts"] = {
            "diagrams": [item.model_dump(mode = "json") for item in synthesis.diagrams],
            "equations": [item.model_dump(mode = "json") for item in synthesis.equations],
            "charts": [item.model_dump(mode = "json") for item in synthesis.charts],
            "timelines": [item.model_dump(mode = "json") for item in synthesis.timelines],
            "maps": [item.model_dump(mode = "json") for item in synthesis.maps],
        }
        try:
            repaired_visuals = await _build_visual_artifacts(
                dependencies,
                state["run_id"],
                ref["chapter_id"],
                visual_request,
            )
        except Exception as error:  # noqa: BLE001 — retain the valid guide if optional repairs fail.
            visual_review_flags.append(
                f"Visual repair could not be completed ({type(error).__name__}); unsupported visuals were omitted."
            )
            logger.warning(
                "[bd-distill] chapter visual repair failed; retaining the valid guide and omitting "
                "unsupported visuals chapter=%s error_type=%s",
                ref["chapter_id"],
                type(error).__name__,
            )
            break
        synthesis = domains.bd.distill.synthesize.domain.merge_visual_artifacts(synthesis, repaired_visuals)
        visual_issues = domains.bd.distill.synthesize.domain.visual_evidence_issues(synthesis, units)

    if visual_issues:
        synthesis, omitted_visual_flags = domains.bd.distill.synthesize.domain.omit_unsupported_visuals(
            synthesis, visual_issues,
        )
        visual_review_flags.extend(omitted_visual_flags)
        logger.warning(
            "[bd-distill] omitted unsupported chapter visuals chapter=%s items=%s",
            ref["chapter_id"],
            visual_review_flags,
        )
    artifact.update({
        "synthesis": synthesis.model_dump(mode = "json"),
        "visual_review_flags": list(dict.fromkeys(visual_review_flags)),
        "visuals_status": "unavailable" if visual_generation_unavailable else "complete",
    })
    await storage.write(
        ref["artifact_key"],
        domains.bd.distill.domain.json_body(artifact),
        content_type = domains.bd.distill.params.JSON_TYPE,
    )
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "chapter_visuals",
        status = "done",
        chapter_id = ref["chapter_id"],
    )
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "chapter",
        status = "done",
        chapter_id = ref["chapter_id"],
    )
    return {"completed_chapters": [ref]}
