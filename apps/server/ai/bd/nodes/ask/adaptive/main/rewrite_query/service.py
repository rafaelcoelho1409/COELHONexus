"""Bounded query rewriting after an unsuccessful evidence search."""
from __future__ import annotations
import domains
from ...... import agents, graphs

from langgraph.runtime import Runtime


async def rewrite_queries(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    max_questions = graphs.ask.adaptive.params.MAX_RESEARCH_QUESTIONS
    domains.settings.llm_counter.service.set_node(node = "rewrite_query")
    plan = await agents.ask.service.invoke(
        runtime.context.planner,
        agents.ask.schemas.AskPlan,
        {
            "book_id": runtime.context.book_id,
            "question": state.get("question", ""),
            "conversation_history": state.get("conversation_history", [])[-4:],
            "candidate_sub_questions": state.get("queries", [])[:max_questions],
            "retrieval_feedback": (
                "The first scoped search returned no usable records. "
                "Rewrite using literal book vocabulary and concise searchable phrases."
            ),
        },
        skill_role = "ask_planner",
    )
    queries = plan.sub_questions or [str(state.get("question", ""))]
    return {
        "queries": queries[:max_questions],
        "retry_count": 1,
        "retrieval_error": "",
    }
