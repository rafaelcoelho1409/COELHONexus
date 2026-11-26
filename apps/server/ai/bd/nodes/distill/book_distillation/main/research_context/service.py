"""Gather optional external research and persist its attributed context."""
from __future__ import annotations
import domains
from ...... import graphs, skills

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


def _web_search_research_hints(state: dict[str, Any]) -> list[dict[str, str]]:
    """Bound retrieval results so the Research Analyst can verify promising search leads."""
    result = state.get("web_search_result")
    records = result.get("evidence") if isinstance(result, dict) else None
    if not isinstance(records, list):
        return []
    hints = []
    for record in records[:6]:
        if not isinstance(record, dict):
            continue
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        hint = {"text": text[:1200]}
        for key in ("id", "title", "url"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                hint[key] = value[:500]
        hints.append(hint)
    return hints


async def _write_research_context(
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
    state: dict[str, Any],
    synthesis: domains.bd.distill.ai.schemas.BookSynthesis,
) -> str:
    key = domains.bd.distill.ai.keys.research_context_key(state["book_id"], state["run_id"])
    if await dependencies.storage.exists(key):
        context = domains.bd.distill.ai.schemas.ResearchContext.model_validate_json(
            await dependencies.storage.read_text(key),
        )
        return key

    if not dependencies.research_enabled:
        context = domains.bd.distill.ai.schemas.ResearchContext(status = "disabled")
    else:
        title = str(state.get("title") or "Book")
        search_session = dependencies.research_search_session
        analysis = None
        retrieval_context = None
        if not isinstance(state.get("web_search_result"), dict):
            retrieval_context = await domains.bd.distill.ai.retrieval_context.service.read_persisted_retrieval_context(
                dependencies.storage,
                state["book_id"],
                state["run_id"],
            )
        if isinstance(retrieval_context, dict):
            state = {**state, **retrieval_context}
        try:
            research_payload = {
                "book_title": title,
                "book_synthesis": domains.bd.distill.ai.synthesize.domain.book_summary(synthesis),
                "preliminary_search_results": _web_search_research_hints(state),
                "preliminary_search_results_instructions": (
                    "Treat preliminary results as unverified leads. Open or search the full sources through your configured tools "
                    "and cite only sources and quotations captured in the current Search MCP session."
                ),
            }
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = state["run_id"], node_id = "research_analyst", stage = "distill",
            ):
                result = await domains.settings.chat.service.resilient_ainvoke(
                    lambda: dependencies.research_analyst.ainvoke({
                        "messages": [{
                            "role": "user",
                            "content": json.dumps(research_payload, ensure_ascii = False),
                        }],
                        "files": skills.service.files_for("distill_research_analyst"),
                    }, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }, context = search_session),
                    attempts = domains.bd.distill.ai.params.AGENT_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.ai.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )
            analysis = domains.bd.distill.ai.domain.structured_model_from_agent_result(
                result,
                domains.bd.distill.ai.schemas.ResearchAnalysisOutput,
            )
        except Exception as error:  # noqa: BLE001 — optional external research never fails Distill.
            logger.warning(
                "[bd-distill] research analyst unavailable; retaining collected Search MCP sources "
                "run=%s error_type=%s error=%s",
                state["run_id"],
                type(error).__name__,
                str(error)[:400],
            )

        sources = [
            domains.bd.distill.ai.schemas.ResearchSource.model_validate(item)
            for item in search_session.sources.values()
        ]
        allowed_source_ids = {source.source_id for source in sources}
        readable_source_ids = {
            source_id for source_id in search_session.source_texts
            if source_id in allowed_source_ids
        }
        findings = []
        if isinstance(analysis, domains.bd.distill.ai.schemas.ResearchAnalysisOutput):
            for finding in analysis.findings:
                quote_source_ids = {quote.source_id for quote in finding.evidence_quotes}
                valid_quotes = all(
                    quote.source_id in finding.source_ids
                    and quote.source_id in readable_source_ids
                    and domains.bd.distill.ai.domain.external_quote_is_supported(
                        quote.quote,
                        search_session.source_texts[quote.source_id],
                    )
                    for quote in finding.evidence_quotes
                )
                if (
                    finding.source_ids
                    and set(finding.source_ids) <= readable_source_ids
                    and quote_source_ids == set(finding.source_ids)
                    and finding.evidence_quotes
                    and valid_quotes
                ):
                    findings.append(finding)
            if len(findings) != len(analysis.findings):
                logger.warning(
                    "[bd-distill] omitted external findings without verified full-source quotations run=%s",
                    state["run_id"],
                )
        context = domains.bd.distill.ai.schemas.ResearchContext(
            status = search_session.status,
            provider_id = search_session.provider_id,
            queries = search_session.queries,
            sources = sources,
            findings = findings,
            error_type = search_session.error_type,
        )
    await dependencies.storage.write(
        key,
        domains.bd.distill.ai.domain.json_body(context.model_dump(mode = "json")),
        content_type = domains.bd.distill.ai.params.JSON_TYPE,
    )
    return key


async def research_context(
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    if dependencies.research_enabled:
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "activity",
            node = "research_context",
            current_task = "Gathering dated external context",
        )
    synthesis_record = json.loads(await dependencies.storage.read_text(state["book_synthesis_key"]))
    synthesis = domains.bd.distill.ai.schemas.BookSynthesis.model_validate(synthesis_record["synthesis"])
    # Research uses the source-only book summary regardless of the separately authored decision brief.
    synthesis = synthesis.model_copy(update = {"decision_brief": None})
    key = await _write_research_context(dependencies, state, synthesis)
    return {"research_context_key": key}


