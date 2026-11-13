"""Adaptive effort classification and bounded query planning."""
from __future__ import annotations
import domains
from .... import agents, graphs

from langchain.runtime import Runtime


async def plan_research(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    domains.settings.llm_counter.service.set_node(node = "plan_research")
    plan = await agents.ask.service.invoke(
        runtime.context.planner,
        agents.ask.schemas.AskPlan,
        {
            "book_id": runtime.context.book_id,
            "question": state.get("question", ""),
            "conversation_history": state.get("conversation_history", [])[-6:],
            "candidate_sub_questions": state.get("supplied_sub_questions", [])[:4],
        },
    )
    forced = str(state.get("force_mode", "")).casefold()
    mode = forced if forced in {"fast", "standard", "deep"} else plan.mode
    if mode == "deep":
        questions = plan.sub_questions or state.get("supplied_sub_questions") or [state["question"]]
        questions = list(dict.fromkeys(str(item).strip() for item in questions if str(item).strip()))[:4]
    else:
        questions = [str(state.get("question", ""))]
    return {
        "mode": mode,
        "sub_questions": questions if mode == "deep" else [],
        "queries": questions,
        "research_plan": plan.strategy,
        "retry_count": 0,
    }


async def rewrite_queries(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    domains.settings.llm_counter.service.set_node(node = "rewrite_query")
    plan = await agents.ask.service.invoke(
        runtime.context.planner,
        agents.ask.schemas.AskPlan,
        {
            "book_id": runtime.context.book_id,
            "question": state.get("question", ""),
            "conversation_history": state.get("conversation_history", [])[-4:],
            "candidate_sub_questions": state.get("queries", []),
            "retrieval_feedback": "The first scoped search returned no usable records. Rewrite using literal book vocabulary and concise searchable phrases.",
        },
    )
    queries = plan.sub_questions or [str(state.get("question", ""))]
    return {"queries": queries[:4], "retry_count": 1, "retrieval_error": ""}
