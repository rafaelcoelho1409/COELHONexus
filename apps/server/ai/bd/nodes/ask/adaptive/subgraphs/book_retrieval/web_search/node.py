"""LangGraph adapter for Ask's optional Search MCP retrieval source."""
from __future__ import annotations

from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def retrieve(
    state: graphs.ask.adaptive.subgraphs.book_retrieval.states.BookRetrievalState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    async def execute() -> dict:
        result = await service.search_external_evidence(state, runtime)
        status = str(result.get("external_search_status") or "unavailable")
        query = str(
            result.get("external_search_query")
            or state.get("external_search_query")
            or state.get("question")
            or ""
        ).strip()
        evidence = list(result.get("external_documents") or [])
        return {
            **result,
            "web_search_result": {
                "status": status,
                "source": "web_search",
                "evidence": evidence,
                "queries": [query] if query and status != "not_requested" else [],
                "error_type": result.get("external_search_error"),
            },
        }

    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "web_search",
        execute,
    )
