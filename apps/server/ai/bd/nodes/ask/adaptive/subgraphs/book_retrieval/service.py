"""Ask-specific orchestration for its bounded source retrieval specialists."""
from __future__ import annotations
import domains
from . import params
from ...... import tools

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Literal


logger = logging.getLogger(__name__)
RetrievalSource = Literal["elasticsearch", "qdrant", "neo4j"]


def normalize_queries(values: Any) -> list[str]:
    """Normalize, deduplicate, and bound the planner's book retrieval queries."""
    if not isinstance(values, list):
        return []
    queries = [
        " ".join(str(value).split())[:params.MAX_QUERY_CHARS]
        for value in values
        if str(value).strip()
    ]
    return list(dict.fromkeys(query for query in queries if query))[:params.MAX_QUERIES]


async def execute_source(
    source: RetrievalSource,
    agent_builder: Callable[[Any], Any],
    state: dict[str, Any],
    context: Any,
) -> list[dict[str, Any]]:
    """Run one Ask-owned source agent for each planned query."""
    queries = normalize_queries(state.get("queries"))

    async def retrieve_one(query: str) -> dict[str, Any]:
        session = tools.retrieval.entities.RetrievalSession(
            book_id = context.book_id,
            run_id = context.run_id,
            source = source,
        )
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
                        config = {
                            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                        },
                    ),
                    timeout = params.AGENT_TIMEOUT_S,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning("BD Ask retrieval specialist timed out source=%s", source)
            return {
                "query": query,
                "status": "timeout",
                "evidence": [],
                "error_type": "TimeoutError",
            }
        except Exception as error:  # noqa: BLE001 — sibling sources and queries remain useful.
            logger.warning(
                "BD Ask retrieval specialist failed source=%s error_type=%s",
                source,
                type(error).__name__,
            )
            return {
                "query": query,
                "status": "unavailable",
                "evidence": [],
                "error_type": type(error).__name__,
            }

        evidence = domains.bd.rag.ai.retrieval.service.fuse_evidence(session.ranked_lists)
        status = "complete" if evidence else (
            session.statuses[-1] if session.statuses else "no_results"
        )
        return {
            "query": query,
            "status": status,
            "evidence": evidence,
            "error_type": session.error_type,
        }

    return await asyncio.gather(*(retrieve_one(query) for query in queries))
