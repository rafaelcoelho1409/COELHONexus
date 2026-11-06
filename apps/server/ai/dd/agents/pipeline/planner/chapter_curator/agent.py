"""DeepAgent that splits one oversize chapter, checking its split with tools."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any


def chapter_curator(*, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the curator for one chapter. `tools` are that chapter's `cluster_units` and `validate_split`, bound to its units."""
    return service.build(
        "dd_chapter_curator",
        system_prompt = prompts.pipeline.planner.chapter_curator.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.SplitPlan,
        skill_role = "chapter_curator",
        allowed_tools = frozenset({"read_file"}),
        tools = tools,
        model = model,
        model_calls = params.MODEL_CALLS,
    )
