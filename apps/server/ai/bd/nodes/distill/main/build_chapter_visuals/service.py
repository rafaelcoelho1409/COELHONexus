"""Build and validate visual artifacts for saved chapter guides."""
from __future__ import annotations
import domains
from ..... import graphs, skills

import hashlib
import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def _build_visual_artifacts(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    chapter_id: str,
    payload: dict[str, Any],
) -> domains.bd.distill.ai.schemas.VisualArtifactOutput:
    """Build separately typed visuals, retrying malformed results without weakening citations."""
    attempts = domains.bd.distill.ai.params.CHAPTER_VISUAL_REPAIR_ATTEMPTS + 1
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
                ns = "bd",
                run_id = run_id,
                node_id = "visual_artifact_builder",
                stage = "distill",
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
                    attempts = domains.bd.distill.ai.params.AGENT_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.ai.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )
            visuals = domains.bd.distill.ai.domain.structured_model_from_agent_result(
                result,
                domains.bd.distill.ai.schemas.VisualArtifactOutput,
            )
            if isinstance(visuals, domains.bd.distill.ai.schemas.VisualArtifactOutput):
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
    units = await domains.bd.distill.ai.synthesize.service.read_learning_units(
        storage,
        state["book_id"],
        state["run_id"],
        ref["unit_ids"],
        ref.get("unit_sources") or chapter_input.get("unit_sources"),
    )
    synthesis = domains.bd.distill.ai.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
    allowed_ids = set(ref["unit_ids"])
    visual_request: dict[str, Any] = {
        "section": chapter_input,
        "chapter_synthesis": {
            "overview": synthesis.overview,
            "lessons": [item.model_dump(mode = "json") for item in synthesis.lessons],
            "open_questions": [item.model_dump(mode = "json") for item in synthesis.open_questions],
            "learning_objectives": [item.model_dump(mode = "json") for item in synthesis.learning_objectives],
            "practice_items": [item.model_dump(mode = "json") for item in synthesis.practice_items],
        },
        "learning_units": units,
        "allowed_learning_unit_ids": sorted(allowed_ids),
    }
    visual_review_flags: list[str] = []
    source_page_by_number = {
        page["page_number"]: page
        for page in state.get("source_pages", [])
        if isinstance(page, dict) and type(page.get("page_number")) is int
    }
    candidate_figures = {
        (item.get("figure_name"), item.get("page_number"))
        for item in chapter_input.get("source_figures", [])
        if isinstance(item, dict)
        and isinstance(item.get("figure_name"), str)
        and type(item.get("page_number")) is int
    }
    verified_source_text_by_page: dict[int, str] = {}
    source_figure_context = []
    for figure_name, page_number in sorted(
        (item for item in candidate_figures if isinstance(item[0], str) and type(item[1]) is int),
        key = lambda item: (item[1], item[0]),
    ):
        source_page = source_page_by_number.get(page_number)
        if source_page is None or figure_name not in source_page.get("figures", []):
            continue
        source_text = verified_source_text_by_page.get(page_number)
        if source_text is None:
            try:
                source_text = await storage.read_text(source_page["source_key"])
            except Exception as error:  # noqa: BLE001 — source figures are optional.
                visual_review_flags.append(
                    f"Source figure context on page {page_number} was unavailable ({type(error).__name__})."
                )
                continue
            if hashlib.sha256(source_text.encode("utf-8")).hexdigest() != source_page["source_sha256"]:
                visual_review_flags.append(
                    f"Source figure context on page {page_number} was omitted because its source hash changed."
                )
                continue
            verified_source_text_by_page[page_number] = source_text
        position = source_text.find(figure_name)
        start = max(0, position - 700) if position >= 0 else 0
        end = min(len(source_text), position + 1100) if position >= 0 else min(len(source_text), 1800)
        source_figure_context.append({
            "figure_name": figure_name,
            "page_number": page_number,
            "text": source_text[start:end],
        })
        if len(source_figure_context) == 12:
            break
    visual_request["source_figure_context"] = source_figure_context

    async def retain_verified_source_figures(
        visuals: domains.bd.distill.ai.schemas.VisualArtifactOutput,
    ) -> domains.bd.distill.ai.schemas.VisualArtifactOutput:
        selected = []
        seen = set()
        for figure in visuals.source_figures:
            identity = (figure.figure_name, figure.page_number)
            source_page = source_page_by_number.get(figure.page_number)
            if (
                identity not in candidate_figures
                or identity in seen
                or not set(figure.learning_unit_ids) <= allowed_ids
                or source_page is None
                or figure.figure_name not in source_page.get("figures", [])
            ):
                visual_review_flags.append(
                    f"An unverified source figure on page {figure.page_number} was omitted."
                )
                continue
            source_text = verified_source_text_by_page.get(figure.page_number)
            if source_text is None:
                try:
                    source_text = await storage.read_text(source_page["source_key"])
                except Exception as error:  # noqa: BLE001 — optional figures cannot block other visuals.
                    visual_review_flags.append(
                        f"A source figure on page {figure.page_number} was omitted ({type(error).__name__})."
                    )
                    continue
                if hashlib.sha256(source_text.encode("utf-8")).hexdigest() != source_page["source_sha256"]:
                    visual_review_flags.append(
                        f"A source figure on page {figure.page_number} was omitted because its source page hash changed."
                    )
                    continue
                verified_source_text_by_page[figure.page_number] = source_text
            if not domains.bd.distill.ai.domain.quote_is_supported(figure.caption, source_text):
                visual_review_flags.append(
                    f"A source figure on page {figure.page_number} was omitted because its caption was not found verbatim."
                )
                continue
            seen.add(identity)
            selected.append(figure.model_dump(mode = "python"))
        return domains.bd.distill.ai.schemas.VisualArtifactOutput.model_validate({
            **visuals.model_dump(mode = "python"),
            "source_figures": selected,
        })

    await graphs.service.report(
        dependencies.progress_callback,
        kind = "activity",
        node = "build_chapter_visuals",
        current_task = f"Building visuals: {chapter_input['title']}",
    )
    visual_generation_unavailable = False
    try:
        visual_output = await _build_visual_artifacts(
            dependencies,
            state["run_id"],
            ref["chapter_id"],
            visual_request,
        )
        visual_output = await retain_verified_source_figures(visual_output)
        synthesis = domains.bd.distill.ai.synthesize.domain.merge_visual_artifacts(synthesis, visual_output)
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

    visual_issues = domains.bd.distill.ai.synthesize.domain.visual_evidence_issues(synthesis, units)
    for _ in range(domains.bd.distill.ai.params.CHAPTER_VISUAL_REPAIR_ATTEMPTS):
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
            "source_figures": [item.model_dump(mode = "json") for item in synthesis.source_figures],
        }
        try:
            repaired_visuals = await _build_visual_artifacts(
                dependencies,
                state["run_id"],
                ref["chapter_id"],
                visual_request,
            )
            repaired_visuals = await retain_verified_source_figures(repaired_visuals)
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
        synthesis = domains.bd.distill.ai.synthesize.domain.merge_visual_artifacts(synthesis, repaired_visuals)
        visual_issues = domains.bd.distill.ai.synthesize.domain.visual_evidence_issues(synthesis, units)

    if visual_issues:
        synthesis, omitted_visual_flags = domains.bd.distill.ai.synthesize.domain.omit_unsupported_visuals(
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
        domains.bd.distill.ai.domain.json_body(artifact),
        content_type = domains.bd.distill.ai.params.JSON_TYPE,
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
