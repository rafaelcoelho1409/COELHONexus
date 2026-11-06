"""DeepAgent that repairs the whitespace of one mangled code block, with a parser tool for Python."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware


def code_normalizer(*, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the normalizer. `tools` hold `check_code` (parses Python). Called once per distinct code block: no skill."""
    return service.build(
        "dd_code_normalizer",
        system_prompt = prompts.pipeline.synth.code_normalizer.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.NormalizedCodeAnswer,
        tools = tools,
        middleware_extra = [
            ToolCallLimitMiddleware(tool_name = "check_code", run_limit = config.CHECK_CALL_LIMIT, exit_behavior = "continue"),
        ],
        model = model,
        model_calls = config.MODEL_CALLS,
    )
