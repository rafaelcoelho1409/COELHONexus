"""Factory for the BD evidence-relevance grading DeepAgent."""
from __future__ import annotations

from ..... import bd


def grader(model):
    return bd.agents.ask.service.build(
        model = model, name = "bd_ask_evidence_grader",
        system_prompt = bd.prompts.ask.grader.prompts.SYSTEM_PROMPT,
        response_format = bd.agents.ask.schemas.EvidenceGrades,
        model_call_limit = bd.agents.ask.params.GRADER_MODEL_CALLS,
    )
