"""Factory for the tool-free YCS evidence-grounding critic."""
from __future__ import annotations

import ai


def research_critic(model):
    return ai.ycs.agents.service.build(
        model = model,
        name = "ycs_ask_research_critic",
        system_prompt = ai.ycs.prompts.ask.research_critic.prompts.SYSTEM_PROMPT,
        response_format = ai.ycs.agents.ask.research_critic.schemas.CriticAssessment,
        model_call_limit = ai.ycs.agents.ask.research_critic.params.MAX_MODEL_CALLS,
    )
