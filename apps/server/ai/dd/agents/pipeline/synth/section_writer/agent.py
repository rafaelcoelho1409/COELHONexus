"""DeepAgent that writes one section of a chapter as a cookbook, checked with the validator that judges its answer."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware


STACK_TOOLS = frozenset({"read_file"})


def section_writer(*, backend: Any, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the writer for one section. `backend` mounts the section's code bank on `/vault/`; `tools` hold the section's
    `check_section` (it knows the bank, the valid source keys and the heading). Called once per section: no skill, the rubric is the system prompt."""
    return service.build(
        "dd_section_writer",
        system_prompt = prompts.pipeline.synth.section_writer.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.SectionAnswer,
        allowed_tools = STACK_TOOLS,
        tools = tools,
        backend = backend,
        read_paths = config.READ_PATHS,
        middleware_extra = [
            ToolCallLimitMiddleware(tool_name = "check_section", run_limit = config.CHECK_CALL_LIMIT, exit_behavior = "continue"),
        ],
        model = model,
        model_calls = config.MODEL_CALLS,
    )
