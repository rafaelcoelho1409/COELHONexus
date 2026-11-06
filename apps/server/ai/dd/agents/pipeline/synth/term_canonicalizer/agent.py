"""DeepAgent that settles the terms chapters define differently."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts


def term_canonicalizer(model = None) -> entities.Role:
    """Build the canonicalizer. Called once per study: system prompt only."""
    return service.build(
        "dd_term_canonicalizer",
        system_prompt = prompts.pipeline.synth.term_canonicalizer.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.CanonicalAnswer,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
