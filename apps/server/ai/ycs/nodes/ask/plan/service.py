"""Multi-hop research planning for YCS Ask."""
from __future__ import annotations
import domains
from .... import agents, graphs

import asyncio


async def plan_research(
    state: graphs.ask.adaptive.states.AdaptiveAskState,
    model,
) -> dict:
    """Create bounded research questions from the original user question."""
    domains.settings.llm_counter.service.set_node(node = "plan")
    limit = agents.ask.research_planner.params.MAX_QUESTION_CHARS
    question = str(state.get("question", ""))[:limit]
    existing = [
        str(item)[:limit]
        for item in state.get("sub_questions", [])[:agents.ask.research_planner.params.MAX_EXISTING_QUESTIONS]
        if str(item).strip()
    ]
    payload = (
        f"User question:\n{question}\n\n"
        f"Candidate sub-questions (may be empty):\n{existing or 'None'}"
    )
    agent = agents.ask.research_planner.agent.research_planner(model)
    result = await asyncio.wait_for(
        agents.service.ask(
            agent,
            agents.ask.research_planner.schemas.ResearchPlan,
            payload,
            attempts = agents.ask.research_planner.params.MAX_MODEL_CALLS,
        ),
        timeout = agents.ask.research_planner.params.AGENT_TIMEOUT_S,
    )
    return {"sub_questions": result.sub_questions, "research_plan": result.strategy}
