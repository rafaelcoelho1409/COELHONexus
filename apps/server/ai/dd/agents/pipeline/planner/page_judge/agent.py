"""DeepAgent that returns the KEEP/DROP verdict of one documentation page."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts


def page_judge(model = None) -> entities.Role:
    """Build the judge. Its whole rubric is its system prompt (no skill, no tools): it is called once per page, hundreds of times a run."""
    return service.build(
        "dd_page_judge",
        system_prompt = prompts.pipeline.planner.page_judge.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.PageRelevance,
        model = model,
        model_calls = params.MODEL_CALLS,
    )
