"""Bounded source-specialist execution behind thin LangGraph nodes."""
from __future__ import annotations
import domains
from . import keys, params
from ... import agents, graphs, tools

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)


_RETRIEVER_BY_SOURCE = {
    "elasticsearch": agents.rag.elasticsearch.agent.elasticsearch_retriever,
    "qdrant": agents.rag.qdrant.agent.qdrant_retriever,
    "neo4j": agents.rag.neo4j.retrieval.agent.neo4j_retriever,
    "web_search": agents.rag.web_search.agent.web_search_retriever,
}


async def retrieve_source(
    source: str,
    state: graphs.rag.retrieval.states.RetrievalGraphState,
    runtime: Runtime[graphs.rag.retrieval.entities.RetrievalContext],
) -> dict[str, Any]:
    """Run one source-limited DeepAgent and trust only its MCP tool results."""
    return await retrieve_source_with_context(source, state, runtime.context)


async def retrieve_source_with_context(
    source: str,
    state: Any,
    context: Any,
) -> dict[str, Any]:
    """Select and run a source specialist with an explicit graph context."""
    return await execute_retrieval_agent(
        source,
        _RETRIEVER_BY_SOURCE[source],
        state,
        context,
    )


async def execute_retrieval_agent(
    source: str,
    agent_builder: Callable[[Any], Any],
    state: Any,
    context: Any,
) -> dict[str, Any]:
    """Run an explicitly selected source specialist without source dispatch."""
    result_key = keys.RETRIEVAL_RESULT_KEY_BY_SOURCE[source]
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
        logger.warning("BD retrieval specialist timed out source=%s", source)
        return {result_key: {"status": "timeout", "source": source, "evidence": [], "queries": session.queries}}
    except Exception as error:  # noqa: BLE001 — sibling specialist results remain useful.
        logger.warning(
            "BD retrieval specialist failed source=%s error_type=%s",
            source,
            type(error).__name__,
        )
        return {
            result_key: {
                "status": "unavailable", "source": source, "evidence": [],
                "queries": session.queries, "error_type": type(error).__name__,
            },
        }

    if source == "web_search":
        ranked_evidence = tools.web_search.domain.to_retrieval_evidence(
            list(session.sources.values())
        )
        evidence = domains.bd.rag.ai.retrieval.service.fuse_evidence(
            [ranked_evidence] if ranked_evidence else []
        )
        status = "complete" if evidence else session.status
    else:
        evidence = domains.bd.rag.ai.retrieval.service.fuse_evidence(session.ranked_lists)
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
