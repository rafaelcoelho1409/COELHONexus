"""Factory for the tool-free YCS multi-hop research planner."""
from __future__ import annotations

from ..... import ycs


def research_planner(model):
    return ycs.agents.service.build(
        model = model,
        name = "ycs_ask_research_planner",
        system_prompt = ycs.prompts.ask.research_planner.prompts.SYSTEM_PROMPT,
        response_format = ycs.agents.ask.research_planner.schemas.ResearchPlan,
        model_call_limit = ycs.agents.ask.research_planner.params.MAX_MODEL_CALLS,
    )
