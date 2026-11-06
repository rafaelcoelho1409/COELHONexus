"""DeepAgent that adds prose subtopics for the documentation facts a written section does not state."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts


def source_extender(model = None) -> entities.Role:
    """Build the extender. Its rubric is its system prompt (no skill, no tools): the node validates what it proposes (`parse_source_topup`)."""
    return service.build(
        "dd_source_extender",
        system_prompt = prompts.pipeline.synth.source_extender.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.SourceExtensionAnswer,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
