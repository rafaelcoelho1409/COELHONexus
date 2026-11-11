"""Factory for the book-scoped adaptive planning DeepAgent."""
from __future__ import annotations

import ai


def planner(model):
    return ai.bd.agents.ask.service.build(
        model = model, name = "bd_ask_planner",
        system_prompt = ai.bd.prompts.ask.planner.prompts.SYSTEM_PROMPT,
        response_format = ai.bd.agents.ask.schemas.AskPlan,
        model_call_limit = ai.bd.agents.ask.params.PLANNER_MODEL_CALLS,
    )
