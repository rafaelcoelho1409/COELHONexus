"""Factory for the strict book-evidence critic DeepAgent."""
from __future__ import annotations

import ai


def critic(model):
    return ai.bd.agents.ask.service.build(
        model = model, name = "bd_ask_evidence_critic",
        system_prompt = ai.bd.prompts.ask.critic.prompts.SYSTEM_PROMPT,
        response_format = ai.bd.agents.ask.schemas.EvidenceAssessment,
        model_call_limit = ai.bd.agents.ask.params.CRITIC_MODEL_CALLS,
    )
