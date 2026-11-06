"""DeepAgent that extracts a chapter's atomic claims and key terms."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts


def claim_extractor(model = None) -> entities.Role:
    """Build the extractor. Called once per chapter of a study: system prompt only."""
    return service.build(
        "dd_claim_extractor",
        system_prompt = prompts.pipeline.synth.claim_extractor.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.ClaimsAnswer,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
