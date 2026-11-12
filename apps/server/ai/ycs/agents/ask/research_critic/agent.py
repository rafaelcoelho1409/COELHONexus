"""Factory for the tool-free YCS evidence-grounding critic."""
from __future__ import annotations

from ..... import ycs


def research_critic(model):
    return ycs.agents.service.build(
        model = model,
        name = "ycs_ask_research_critic",
        system_prompt = ycs.prompts.ask.research_critic.prompts.SYSTEM_PROMPT,
        response_format = ycs.agents.ask.research_critic.schemas.CriticAssessment,
        model_call_limit = ycs.agents.ask.research_critic.params.MAX_MODEL_CALLS,
    )
