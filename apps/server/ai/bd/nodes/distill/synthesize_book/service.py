"""Synthesize the whole-book guide from completed chapter guides."""
from __future__ import annotations
import domains
from .... import graphs, prompts, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def _synthesize_book_batch_with_llm(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    batch_id: str,
    prompt: str,
) -> domains.bd.distill.schemas.BookSynthesis:
    """Use the whole-book DeepAgent with bounded retries for its structured contract."""
    system_prompt = prompts.distill.book_synthesizer.prompts.SYSTEM_PROMPT
    user_request = prompt.removeprefix(system_prompt).strip()
    attempts = domains.bd.distill.params.BOOK_AGENT_RESPONSE_ATTEMPTS
    last_error: Exception | None = None
    for attempt in range(attempts):
        payload: dict[str, Any] = {"book_request": user_request}
        if attempt:
            payload["response_retry"] = (
                "The previous answer did not validate as BookSynthesisOutput. Return the complete typed schema now, "
                "with a concise thesis, at most four takeaways, three connections, and two limitations. Cite only "
                "the smallest directly supporting supplied IDs. Do not include a decision_brief."
            )
            if last_error is not None:
                payload["validation_issue"] = f"{type(last_error).__name__}: {str(last_error)[:900]}"
        try:
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = run_id, node_id = "book_synthesizer", stage = "distill",
            ):
                result = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: dependencies.book_synthesizer.ainvoke({
                        "messages": [{
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii = False),
                        }],
                        "files": skills.service.files_for("distill_book_synthesizer"),
                    }, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }),
                    attempts = domains.bd.distill.params.AGENT_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )
            output = domains.bd.distill.domain.book_synthesis_output_from_agent_result(result)
            if output is not None:
                return domains.bd.distill.schemas.BookSynthesis.model_validate(
                    output.model_dump(mode = "json"),
                )
            last_error = ValueError("book DeepAgent returned no valid BookSynthesisOutput")
        except Exception as error:  # noqa: BLE001 — graph retry handles provider failures; schema retries stay local.
            last_error = error
            if domains.settings.chat.domain.is_transient_provider_error(error):
                raise
        logger.warning(
            "[bd-distill] book DeepAgent returned invalid structured output "
            "run=%s batch=%s attempt=%s/%s error_type=%s",
            run_id,
            batch_id,
            attempt + 1,
            attempts,
            type(last_error).__name__ if last_error else "InvalidAgentResponse",
        )
    raise ValueError("book synthesizer returned no valid BookSynthesis after DeepAgent retries") from last_error


async def synthesize_book(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    storage = dependencies.storage
    chapter_summaries = []
    ordered_refs = sorted(
        state.get("completed_chapters", []),
        key = lambda ref: (ref.get("first_page", 0), ref["part_index"]),
    )
    for ref in ordered_refs:
        artifact = json.loads(await storage.read_text(ref["artifact_key"]))
        chapter = domains.bd.distill.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
        chapter_summaries.append({
            "chapter_id": ref["chapter_id"],
            "title": artifact["title"],
            "section_path": artifact["section_path"],
            "first_page": artifact.get("first_page", 0),
            "last_page": artifact.get("last_page", 0),
            "unit_ids": artifact["unit_ids"],
            "summary": {
                "overview": chapter.overview[:1600],
                "overview_attribution": chapter.overview_attribution,
                "overview_learning_unit_ids": chapter.overview_learning_unit_ids,
                "lessons": [
                    {
                        "title": lesson.title,
                        "explanation": lesson.explanation[:600],
                        "attribution": lesson.attribution,
                        "learning_unit_ids": lesson.learning_unit_ids,
                    }
                    for lesson in chapter.lessons[:8]
                ],
                "open_questions": [
                    {
                        "title": question.title,
                        "explanation": question.explanation[:400],
                        "learning_unit_ids": question.learning_unit_ids,
                    }
                    for question in chapter.open_questions[:4]
                ],
            },
        })
    if not chapter_summaries:
        raise ValueError("book synthesis has no completed chapter guides")

    current = chapter_summaries
    level = 0
    while True:
        batches = [
            current[index:index + domains.bd.distill.params.MAX_BOOK_SYNTHESIS_BATCH_SIZE]
            for index in range(0, len(current), domains.bd.distill.params.MAX_BOOK_SYNTHESIS_BATCH_SIZE)
        ]
        next_level = []
        for batch_index, batch in enumerate(batches):
            await graphs.service.report(
                dependencies.progress_callback,
                kind = "activity",
                node = "synthesize_book",
                current_task = (
                    f"Connecting book sections · level {level + 1}, "
                    f"batch {batch_index + 1} of {len(batches)}"
                ),
            )
            key = domains.bd.distill.keys.synthesis_key(state["book_id"], state["run_id"], level, batch_index)
            if await storage.exists(key):
                record = json.loads(await storage.read_text(key))
                if record.get("inputs") != [item.get("chapter_id", item.get("synthesis_id")) for item in batch]:
                    raise ValueError("a persisted whole-book synthesis batch has different inputs")
                synthesis = domains.bd.distill.schemas.BookSynthesis.model_validate(record["synthesis"])
                self_ids = set(record["unit_ids"])
                referenced_ids = set(synthesis.central_thesis_learning_unit_ids)
                for item in [*synthesis.key_takeaways, *synthesis.cross_chapter_connections, *synthesis.limitations]:
                    referenced_ids.update(item.learning_unit_ids)
                if synthesis.decision_brief is not None:
                    referenced_ids.update(synthesis.decision_brief.recommendation_learning_unit_ids)
                    for item in [*synthesis.decision_brief.options, *synthesis.decision_brief.action_plan]:
                        referenced_ids.update(item.learning_unit_ids)
                if referenced_ids - self_ids:
                    raise ValueError("a persisted whole-book synthesis contains unresolved source IDs")
                next_level.append({
                    **record,
                    "summary": domains.bd.distill.synthesize.domain.book_summary(synthesis),
                })
                continue

            unit_ids = sorted({unit_id for item in batch for unit_id in item["unit_ids"]})
            citation_ids, prompt_guides = domains.bd.distill.synthesize.domain.book_synthesis_context(
                batch,
                domains.bd.distill.params.MAX_BOOK_SYNTHESIS_CONTEXT_CITATION_IDS,
            )
            if not citation_ids:
                raise ValueError("whole-book synthesis batch has no cited summary evidence")
            synthesis_scope = "whole_book" if len(batches) == 1 else "partial_book_batch"
            prompt = (
                prompts.distill.book_synthesizer.prompts.SYSTEM_PROMPT
                + "\n\nSynthesize the provided section guides. Every central thesis, takeaway, connection, and limitation must cite supplied learning-unit IDs. Use only IDs in available_learning_unit_ids; the list is intentionally compact and representative.\n"
                + json.dumps({
                    "scope": synthesis_scope,
                    "available_learning_unit_ids": citation_ids,
                    "section_guides": prompt_guides,
                }, ensure_ascii = False)
            )
            synthesis = await _synthesize_book_batch_with_llm(
                dependencies,
                state["run_id"],
                f"{level}-{batch_index}",
                prompt,
            )
            referenced_ids = set(synthesis.central_thesis_learning_unit_ids)
            for item in [*synthesis.key_takeaways, *synthesis.cross_chapter_connections, *synthesis.limitations]:
                referenced_ids.update(item.learning_unit_ids)
            if synthesis.decision_brief is not None:
                referenced_ids.update(synthesis.decision_brief.recommendation_learning_unit_ids)
                for item in [*synthesis.decision_brief.options, *synthesis.decision_brief.action_plan]:
                    referenced_ids.update(item.learning_unit_ids)
            if referenced_ids - set(citation_ids):
                raise ValueError("whole-book synthesis referenced a learning unit outside its supplied guides")
            record = {
                "synthesis_id": f"{level}-{batch_index}",
                "inputs": [item.get("chapter_id", item.get("synthesis_id")) for item in batch],
                "unit_ids": unit_ids,
                "synthesis": synthesis.model_dump(mode = "json"),
            }
            await storage.write(
                key,
                domains.bd.distill.domain.json_body(record),
                content_type = domains.bd.distill.params.JSON_TYPE,
            )
            next_level.append({
                **record,
                "summary": domains.bd.distill.synthesize.domain.book_summary(synthesis),
            })
        if len(next_level) == 1:
            synthesis_key = domains.bd.distill.keys.synthesis_key(
                state["book_id"], state["run_id"], level, 0,
            )
            return {"book_synthesis_key": synthesis_key}
        current = next_level
        level += 1
        if level > 8:
            raise ValueError("whole-book synthesis exceeded the hierarchy depth limit")


