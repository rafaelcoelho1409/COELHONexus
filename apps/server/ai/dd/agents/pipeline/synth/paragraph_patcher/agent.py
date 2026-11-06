"""DeepAgent that makes a minimal edit to one paragraph of a chapter."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts


def paragraph_patcher(model = None) -> entities.Role:
    """Build the patcher. One paragraph per call: system prompt only; the node accepts the edit only when `paragraph_patch_ok` does."""
    return service.build(
        "dd_paragraph_patcher",
        system_prompt = prompts.pipeline.synth.paragraph_patcher.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.ParagraphAnswer,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
