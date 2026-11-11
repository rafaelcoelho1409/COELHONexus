"""Factory for the evidence-citing BD answer DeepAgent."""
from __future__ import annotations

import ai


def answerer(model):
    return ai.bd.agents.ask.service.build(
        model = model, name = "bd_ask_evidence_answerer",
        system_prompt = ai.bd.prompts.ask.answer.prompts.SYSTEM_PROMPT,
        response_format = ai.bd.agents.ask.schemas.EvidenceAnswer,
        model_call_limit = ai.bd.agents.ask.params.ANSWER_MODEL_CALLS,
    )
