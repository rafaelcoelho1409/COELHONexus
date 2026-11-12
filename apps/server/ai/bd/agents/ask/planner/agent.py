"""Factory for the book-scoped adaptive planning DeepAgent."""
from __future__ import annotations

from ..... import bd


def planner(model):
    return bd.agents.ask.service.build(
        model = model, name = "bd_ask_planner",
        system_prompt = bd.prompts.ask.planner.prompts.SYSTEM_PROMPT,
        response_format = bd.agents.ask.schemas.AskPlan,
        model_call_limit = bd.agents.ask.params.PLANNER_MODEL_CALLS,
    )
