"""DeepAgent that distills one documentation page into a summary, key terms and a role."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts


def page_distiller(model = None) -> entities.Role:
    """Build the distiller. Its rubric is its system prompt (no skill, no tools): it is called once per page."""
    return service.build(
        "dd_page_distiller",
        system_prompt = prompts.pipeline.planner.page_distiller.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.DocDistillateRaw,
        model = model,
        model_calls = params.MODEL_CALLS,
    )
