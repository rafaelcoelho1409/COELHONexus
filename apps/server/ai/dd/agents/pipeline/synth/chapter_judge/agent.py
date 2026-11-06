"""DeepAgent that judges one rendered chapter against the five checklist criteria."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts


def chapter_judge(model = None) -> entities.Role:
    """Build the judge. Its criteria are its system prompt (no skill, no tools): it reads the chapter and the digest in the request."""
    return service.build(
        "dd_chapter_judge",
        system_prompt = prompts.pipeline.synth.chapter_judge.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.JudgeAnswer,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
