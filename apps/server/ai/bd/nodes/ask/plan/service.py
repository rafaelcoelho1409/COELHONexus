"""Adaptive effort classification and bounded query planning."""
from __future__ import annotations
import domains
from .... import agents, graphs

from langgraph.runtime import Runtime


MAX_RESEARCH_QUESTIONS = graphs.ask.adaptive.params.MAX_RESEARCH_QUESTIONS


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
            "candidate_sub_questions": state.get("supplied_sub_questions", [])[
                :MAX_RESEARCH_QUESTIONS
            ],
        },
        skill_role = "ask_planner",
    )
    forced = str(state.get("force_mode", "")).casefold()
    mode = forced if forced in {"fast", "standard", "deep"} else plan.mode
    question = str(state.get("question", "")).strip()
    candidates = (
        plan.sub_questions or state.get("supplied_sub_questions") or [question]
    )[:MAX_RESEARCH_QUESTIONS]
    candidates = list(dict.fromkeys(str(item).strip() for item in candidates if str(item).strip()))
    if mode == "fast":
        queries = [question] if question else []
    elif mode == "standard":
        queries = candidates[:2] or ([question] if question else [])
    else:
        queries = candidates[:MAX_RESEARCH_QUESTIONS] or ([question] if question else [])
    intent = plan.intent
    use_external_search = plan.use_external_search and intent == "book"
    return {
        "mode": mode,
        "intent": intent,
        "use_external_search": use_external_search,
        "external_search_query": plan.external_search_query.strip() or question,
        "sub_questions": queries if mode == "deep" else [],
        "queries": queries,
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
            "candidate_sub_questions": state.get("queries", [])[
                :MAX_RESEARCH_QUESTIONS
            ],
            "retrieval_feedback": "The first scoped search returned no usable records. Rewrite using literal book vocabulary and concise searchable phrases.",
        },
        skill_role = "ask_planner",
    )
    queries = plan.sub_questions or [str(state.get("question", ""))]
    return {"queries": queries[:MAX_RESEARCH_QUESTIONS], "retry_count": 1, "retrieval_error": ""}
