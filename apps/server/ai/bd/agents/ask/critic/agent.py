"""Factory for the strict book-evidence critic DeepAgent."""
from __future__ import annotations

from ..... import bd


def critic(model):
    return bd.agents.ask.service.build(
        model = model, name = "bd_ask_evidence_critic",
        system_prompt = bd.prompts.ask.critic.prompts.SYSTEM_PROMPT,
        response_format = bd.agents.ask.schemas.EvidenceAssessment,
        model_call_limit = bd.agents.ask.params.CRITIC_MODEL_CALLS,
    )
