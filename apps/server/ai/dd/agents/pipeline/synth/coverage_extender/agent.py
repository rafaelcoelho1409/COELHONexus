"""DeepAgent that adds subtopics for the code blocks a written section left unused."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts


def coverage_extender(model = None) -> entities.Role:
    """Build the extender. Its rubric is its system prompt (no skill, no tools): the node validates what it proposes (`parse_topup`)."""
    return service.build(
        "dd_coverage_extender",
        system_prompt = prompts.pipeline.synth.coverage_extender.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.ExtensionAnswer,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
