"""DeepAgent that decides which sections of a failed chapter are written again, reading them first."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any


def chapter_reviewer(*, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the reviewer for one chapter. `tools` hold the chapter's `inspect_section`; its skill says how to map a failure to sections."""
    return service.build(
        "dd_chapter_reviewer",
        system_prompt = prompts.pipeline.synth.chapter_reviewer.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.RevisionAnswer,
        skill_role = "chapter_reviewer",
        tools = tools,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
