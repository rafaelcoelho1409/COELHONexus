"""Factory for the tool-free YCS multi-hop research planner."""
from __future__ import annotations

import ai


def research_planner(model):
    return ai.ycs.agents.service.build(
        model = model,
        name = "ycs_ask_research_planner",
        system_prompt = ai.ycs.prompts.ask.research_planner.prompts.SYSTEM_PROMPT,
        response_format = ai.ycs.agents.ask.research_planner.schemas.ResearchPlan,
        model_call_limit = ai.ycs.agents.ask.research_planner.params.MAX_MODEL_CALLS,
    )
