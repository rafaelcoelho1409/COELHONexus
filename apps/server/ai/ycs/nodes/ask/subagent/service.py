"""Bounded fan-out orchestration for YCS Ask subagents."""
from __future__ import annotations

import asyncio
import logging
import os

import domains
from .... import graphs


logger = logging.getLogger(__name__)
# Shared across concurrent Ask requests; initialize lazily to bind it to the
# active event loop rather than the import-time loop.
_subagent_semaphore: asyncio.Semaphore | None = None


def _resolve_subagent_concurrency() -> int:
    """Read the concurrency override, keeping the semaphore size positive."""
    if "KD_SUBAGENT_CONCURRENCY" in os.environ:
        try:
            return max(1, int(os.environ["KD_SUBAGENT_CONCURRENCY"]))
        except (TypeError, ValueError):
            pass
    return max(1, domains.ycs.ask.rag.adaptive.params.SUBAGENT_CONCURRENCY)


def _get_subagent_semaphore() -> asyncio.Semaphore:
    """Create one process-wide concurrency gate on the first subagent run."""
    global _subagent_semaphore
    if _subagent_semaphore is None:
        concurrency = _resolve_subagent_concurrency()
        _subagent_semaphore = asyncio.Semaphore(concurrency)
        logger.info(
            "[ycs:adaptive] sub-agent concurrency cap = %s%s",
            concurrency,
            " (KD_SUBAGENT_CONCURRENCY override active)"
            if "KD_SUBAGENT_CONCURRENCY" in os.environ else "",
        )
    return _subagent_semaphore


async def run_subagents(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    retriever,
    grader,
    llm,
    checkpointer = None,
) -> dict:
    """Fan out research under a shared semaphore and an outer deadline."""
    channel_ids = state.get("channel_ids") or []
    parent_question = state.get("question", "") or ""
    route = state.get("route") or "search"
    thread_id = state.get("thread_id") or ""

    async def run_one(sub_question: str) -> dict:
        async with _get_subagent_semaphore():
            standard_graph = domains.ycs.ask.rag.standard.graph.build_youtube_rag_graph(
                retriever = retriever,
                grader = grader,
                llm = llm,
                checkpointer = checkpointer,
                channel_ids = channel_ids or None,
            )
            return await domains.ycs.ask.rag.adaptive.nodes.subagent.node.run_subagent(
                {
                    "sub_question": sub_question,
                    "parent_question": parent_question,
                    "channel_ids": channel_ids,
                    "route": route,
                    "thread_id": thread_id,
                },
                standard_graph,
                llm = llm,
            )

    return await domains.ycs.ask.rag.adaptive.nodes.subagent.node.run_subagents_bounded(
        state.get("sub_questions") or [],
        run_one = run_one,
        deadline_s = domains.ycs.ask.rag.adaptive.params.DEEP_FANOUT_DEADLINE_S,
        route = route,
    )
