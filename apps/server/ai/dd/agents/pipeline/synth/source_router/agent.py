"""DeepAgent that routes one source page to the sections of a chapter's outline."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware


def source_router(*, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the router for one source. Its rubric is its system prompt (called once per source: no skill); `tools` hold the
    source's `check_digest`, which knows the outline's ids and the source's hashes."""
    return service.build(
        "dd_source_router",
        system_prompt = prompts.pipeline.synth.source_router.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.DigestAnswer,
        tools = tools,
        middleware_extra = [
            ToolCallLimitMiddleware(tool_name = "check_digest", run_limit = config.CHECK_CALL_LIMIT, exit_behavior = "continue"),
        ],
        model = model,
        model_calls = config.MODEL_CALLS,
    )
