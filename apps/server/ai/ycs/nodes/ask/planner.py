"""DeepAgent-backed planning node for the YCS adaptive graph."""
from __future__ import annotations

import asyncio

import ai
import domains


async def plan_research(state: dict, model) -> dict:
    domains.settings.llm_counter.service.set_node(node = "plan")
    limit = ai.ycs.agents.ask.research_planner.params.MAX_QUESTION_CHARS
    question = str(state.get("question", ""))[:limit]
    existing = [
        str(item)[:limit]
        for item in state.get("sub_questions", [])[:ai.ycs.agents.ask.research_planner.params.MAX_EXISTING_QUESTIONS]
        if str(item).strip()
    ]
    payload = (
        f"User question:\n{question}\n\n"
        f"Candidate sub-questions (may be empty):\n{existing or 'None'}"
    )
    agent = ai.ycs.agents.ask.research_planner.agent.research_planner(model)
    result = await asyncio.wait_for(
        ai.ycs.agents.service.ask(
            agent,
            ai.ycs.agents.ask.research_planner.schemas.ResearchPlan,
            payload,
            attempts = 2,
        ),
        timeout = ai.ycs.agents.ask.research_planner.params.AGENT_TIMEOUT_S,
    )
    return {"sub_questions": result.sub_questions, "research_plan": result.strategy}
