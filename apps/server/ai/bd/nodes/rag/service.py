"""Bounded source-specialist execution behind thin LangGraph nodes."""
from __future__ import annotations
import domains
from . import keys, params
from ... import agents, graphs, tools

import asyncio
import logging
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)


_RETRIEVER_BY_SOURCE = {
    "elasticsearch": agents.rag.elasticsearch.agent.elasticsearch_retriever,
    "qdrant": agents.rag.qdrant.agent.qdrant_retriever,
    "neo4j": agents.rag.neo4j.retrieval.agent.neo4j_retriever,
    "search_mcp": agents.rag.search.agent.search_retriever,
}


async def retrieve_source(
    source: str,
    state: graphs.rag.retrieval.states.RetrievalGraphState,
    runtime: Runtime[graphs.rag.retrieval.entities.RetrievalContext],
) -> dict[str, Any]:
    """Run one source-limited DeepAgent and trust only its MCP tool results."""
    result_key = keys.RETRIEVAL_RESULT_KEY_BY_SOURCE[source]
    if source == "search_mcp" and not state.get("include_web"):
        return {result_key: {"status": "not_requested", "source": source, "evidence": [], "queries": []}}
    context = runtime.context
    session = tools.rag.entities.RetrievalSession(
        book_id = context.book_id,
        run_id = context.run_id,
        source = source,
    )
    query = str(state.get("query") or "")[:params.MAX_QUERY_CHARS]
    try:
        agent_builder = _RETRIEVER_BY_SOURCE[source]
        agent = agent_builder(context.model)
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd", run_id = context.run_id, node_id = f"retrieval_{source}", stage = "retrieval",
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
