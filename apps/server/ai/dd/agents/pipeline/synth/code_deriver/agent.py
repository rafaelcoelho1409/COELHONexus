"""DeepAgent that expands a thin documentation reference into a runnable example, checked by a parser tool."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware


def code_deriver(*, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the deriver. `tools` hold `check_code` (parses the example, checks the length band). One example per thin subtopic: no skill."""
    return service.build(
        "dd_code_deriver",
        system_prompt = prompts.pipeline.synth.code_deriver.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.DerivedCodeAnswer,
        tools = tools,
        middleware_extra = [
            ToolCallLimitMiddleware(tool_name = "check_code", run_limit = config.CHECK_CALL_LIMIT, exit_behavior = "continue"),
        ],
        model = model,
        model_calls = config.MODEL_CALLS,
    )
