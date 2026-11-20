"""Write a separate, source-cited decision brief from the whole-book synthesis."""
from __future__ import annotations

import json
import logging
from typing import Any

import domains

from .... import graphs, skills

logger = logging.getLogger(__name__)


async def write_decision_brief(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "activity",
        node = "write_decision_brief",
        current_task = "Writing source-cited recommendations",
    )
    decision_key = domains.bd.distill.keys.decision_brief_key(state["book_id"], state["run_id"])
    if await dependencies.storage.exists(decision_key):
        domains.bd.distill.schemas.DecisionBriefOutput.model_validate_json(
            await dependencies.storage.read_text(decision_key),
        )
        return {"decision_brief_key": decision_key}

    try:
        decision = await _write_decision_brief(dependencies, state)
    except Exception as error:  # noqa: BLE001 — an optional brief must not block the Learning Guide.
        logger.warning(
            "[bd-distill] decision-brief writer unavailable; publishing source-grounded book guide "
            "run=%s error_type=%s",
            state["run_id"],
            type(error).__name__,
        )
        decision = None

    await _save_decision_brief(
        dependencies,
        decision_key,
        domains.bd.distill.schemas.DecisionBriefOutput(decision_brief = decision),
    )
    return {"decision_brief_key": decision_key}


async def _save_decision_brief(
    dependencies: graphs.distill.entities.DistillDependencies,
    decision_key: str,
    output: domains.bd.distill.schemas.DecisionBriefOutput,
) -> None:
    await dependencies.storage.write(
        decision_key,
        domains.bd.distill.domain.json_body(output.model_dump(mode = "json")),
        content_type = domains.bd.distill.params.JSON_TYPE,
    )


async def _write_decision_brief(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> domains.bd.distill.schemas.BookDecisionBrief | None:
    """Write a cited brief without mutating the whole-book synthesis artifact."""
    synthesis_record = json.loads(await dependencies.storage.read_text(state["book_synthesis_key"]))
    synthesis = domains.bd.distill.schemas.BookSynthesis.model_validate(synthesis_record["synthesis"])
    synthesis = synthesis.model_copy(update = {"decision_brief": None})
    allowed_ids = list(dict.fromkeys(
        [*synthesis.central_thesis_learning_unit_ids]
        + [unit_id for item in [*synthesis.key_takeaways, *synthesis.cross_chapter_connections, *synthesis.limitations]
           for unit_id in item.learning_unit_ids]
    ))
    allowed_id_set = set(allowed_ids)
    if not allowed_ids:
        logger.info("[bd-distill] no cited whole-book evidence is available for a decision brief run=%s", state["run_id"])
        return None
    chapter_refs = state.get("synthesized_chapters") or state.get("completed_chapters", [])
    unit_sources = [
        source
        for ref in chapter_refs
        for source in ref.get("unit_sources", [])
        if source.get("learning_unit_id") in allowed_id_set
    ]
    units = await domains.bd.distill.synthesize.service.read_learning_units(
        dependencies.storage,
        state["book_id"],
        state["run_id"],
        allowed_ids,
        unit_sources,
    )
    payload = {
        "book_synthesis": synthesis.model_dump(mode = "json"),
        "allowed_learning_unit_ids": allowed_ids,
        "learning_units": units,
    }
    with domains.settings.llm_counter.service.llm_context(
        ns = "bd", run_id = state["run_id"], node_id = "decision_brief_writer", stage = "distill",
    ):
        result = await domains.settings.chat.service.resilient_ainvoke(
            lambda: dependencies.decision_brief_writer.ainvoke({
                "messages": [{
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii = False),
                }],
                "files": skills.service.files_for("distill_decision_brief_writer"),
            }, config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            }),
            attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
            wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
        )
    output = domains.bd.distill.domain.structured_model_from_agent_result(
        result,
        domains.bd.distill.schemas.DecisionBriefOutput,
    )
    if not isinstance(output, domains.bd.distill.schemas.DecisionBriefOutput):
        raise TypeError("decision-brief DeepAgent returned no valid DecisionBriefOutput")
    decision = output.decision_brief
    if decision is not None:
        cited_ids = set(decision.recommendation_learning_unit_ids)
        for item in [*decision.options, *decision.action_plan]:
            cited_ids.update(item.learning_unit_ids)
        if not cited_ids or cited_ids - set(allowed_ids):
            logger.warning(
                "[bd-distill] omitted decision brief with unresolved citations run=%s",
                state["run_id"],
            )
            return None
    return decision
