"""DeepAgent that outlines one chapter of a framework's study book, checked with a validation tool and the chapter's source pages."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware


STACK_TOOLS = frozenset({"read_file", "grep", "glob"})


def chapter_outliner(*, backend: Any, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the outliner for one chapter. `backend` mounts the chapter's source pages on `/sources/`; `tools` hold the chapter's
    `validate_outline`. It reads its skill and the pages it needs, checks the outline and writes nothing."""
    return service.build(
        "dd_chapter_outliner",
        system_prompt = prompts.pipeline.synth.chapter_outliner.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.OutlineAnswer,
        skill_role = "chapter_outliner",
        allowed_tools = STACK_TOOLS,
        tools = tools,
        backend = backend,
        read_paths = config.READ_PATHS,
        middleware_extra = [
            ToolCallLimitMiddleware(tool_name = "validate_outline", run_limit = config.CHECK_CALL_LIMIT, exit_behavior = "continue"),
        ],
        model = model,
        model_calls = config.MODEL_CALLS,
    )
