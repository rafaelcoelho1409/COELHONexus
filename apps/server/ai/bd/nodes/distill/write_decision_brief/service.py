"""Write a separate, source-cited decision brief from the whole-book synthesis."""
from __future__ import annotations
import domains
from .... import graphs, skills

import json
import logging
from typing import Any


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
    synthesis_key = state["book_synthesis_key"]
    synthesis_record = json.loads(await dependencies.storage.read_text(synthesis_key))
    await _write_decision_brief(dependencies, state, synthesis_key, synthesis_record)
    return {}


async def _write_decision_brief(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
    synthesis_key: str,
    synthesis_record: dict[str, Any],
) -> None:
    """Write a distinct, source-cited decision brief after the hierarchical book synthesis."""
    synthesis = domains.bd.distill.schemas.BookSynthesis.model_validate(synthesis_record["synthesis"])
    synthesis = synthesis.model_copy(update = {"decision_brief": None})
    synthesis_record = {**synthesis_record, "synthesis": synthesis.model_dump(mode = "json")}
    await dependencies.storage.write(
        synthesis_key,
        domains.bd.distill.domain.json_body(synthesis_record),
        content_type = domains.bd.distill.params.JSON_TYPE,
    )
    allowed_ids = list(dict.fromkeys(
        [*synthesis.central_thesis_learning_unit_ids]
        + [unit_id for item in [*synthesis.key_takeaways, *synthesis.cross_chapter_connections, *synthesis.limitations]
           for unit_id in item.learning_unit_ids]
    ))
    allowed_id_set = set(allowed_ids)
    if not allowed_ids:
        logger.info("[bd-distill] no cited whole-book evidence is available for a decision brief run=%s", state["run_id"])
        return
    unit_sources = [
        source
        for ref in state.get("completed_chapters", [])
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
    try:
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
            raise ValueError("decision-brief DeepAgent returned no valid DecisionBriefOutput")
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
                decision = None
        synthesis = synthesis.model_copy(update = {"decision_brief": decision})
        synthesis_record = {**synthesis_record, "synthesis": synthesis.model_dump(mode = "json")}
        await dependencies.storage.write(
            synthesis_key,
            domains.bd.distill.domain.json_body(synthesis_record),
            content_type = domains.bd.distill.params.JSON_TYPE,
        )
    except Exception as error:  # noqa: BLE001 — a decision brief is optional; keep the core guide publishable.
        logger.warning(
            "[bd-distill] decision-brief writer unavailable; publishing source-grounded book guide "
            "run=%s error_type=%s",
            state["run_id"],
            type(error).__name__,
        )
