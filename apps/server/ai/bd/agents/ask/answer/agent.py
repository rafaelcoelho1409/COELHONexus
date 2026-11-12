"""Factory for the evidence-citing BD answer DeepAgent."""
from __future__ import annotations

from ..... import bd


def answerer(model):
    return bd.agents.ask.service.build(
        model = model, name = "bd_ask_evidence_answerer",
        system_prompt = bd.prompts.ask.answer.prompts.SYSTEM_PROMPT,
        response_format = bd.agents.ask.schemas.EvidenceAnswer,
        model_call_limit = bd.agents.ask.params.ANSWER_MODEL_CALLS,
    )
