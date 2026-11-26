"""Execute bounded source specialists for the Distill book retrieval graph."""
from __future__ import annotations
import domains
from . import domain, params
from ...... import tools

import asyncio
import logging
from collections.abc import Callable
from typing import Any


logger = logging.getLogger(__name__)

async def execute_retrieval_agent(
    source: str,
    agent_builder: Callable[[Any], Any],
    state: Any,
    context: Any,
) -> dict[str, Any]:
    """Run one source-limited Distill agent and return its validated tool results."""
    result_key = domains.bd.distill.ai.params.RETRIEVAL_RESULT_KEY_BY_SOURCE[source]
    if source == "web_search" and not state.get("include_web"):
        return {result_key: {"status": "not_requested", "source": source, "evidence": [], "queries": []}}

    if source == "web_search":
        session = tools.web_search.entities.SearchSession(run_id = context.run_id)
    else:
        session = tools.retrieval.entities.RetrievalSession(
            book_id = context.book_id,
            run_id = context.run_id,
            source = source,
        )

    query = str(state.get("query") or "")[:params.MAX_QUERY_CHARS]
    try:
        agent = agent_builder(context.model)
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd",
            run_id = context.counter_run_id or context.run_id,
            node_id = f"{context.counter_node_prefix}_{source}",
            stage = "retrieval",
        ):
            await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": query}]},
                    context = session,
                    config = {"callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()]},
                ),
                timeout = params.AGENT_TIMEOUT_S,
            )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        logger.warning("BD Distill retrieval specialist timed out source=%s", source)
        return {result_key: {"status": "timeout", "source": source, "evidence": [], "queries": session.queries}}
    except Exception as error:  # noqa: BLE001 — sibling source results remain useful.
        logger.warning(
            "BD Distill retrieval specialist failed source=%s error_type=%s",
            source,
            type(error).__name__,
        )
        return {
            result_key: {
                "status": "unavailable",
                "source": source,
                "evidence": [],
                "queries": session.queries,
                "error_type": type(error).__name__,
            },
        }

    if source == "web_search":
        ranked_evidence = tools.web_search.domain.to_retrieval_evidence(
            list(session.sources.values())
        )
        evidence = domain.fuse_ranked_evidence([ranked_evidence] if ranked_evidence else [])
        status = "complete" if evidence else session.status
    else:
        evidence = domain.fuse_ranked_evidence(session.ranked_lists)
        status = "complete" if evidence else (
            session.statuses[-1] if session.statuses else "no_results"
        )

    return {
        result_key: {
            "status": status,
            "source": source,
            "evidence": evidence,
            "queries": session.queries,
            "error_type": session.error_type,
        },
    }
