"""DeepAgent that scores one documentation page against every chapter of a plan."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts


def page_scorer(proposals: list[dict], *, fixed_keys: bool, model = None) -> entities.Role:
    """Build the scorer for one run. The chapters are part of its system prompt, so it is identical for every page of the run.

    `fixed_keys` asks for ONE required key per chapter (`c0` .. `cN`) instead of a list of scores: a missing key is a validation
    error the model cannot silently commit, and on a large plan the list form came back cut short for most pages."""
    return service.build(
        "dd_page_scorer",
        system_prompt = prompts.pipeline.planner.page_scorer.prompts.system_prompt(proposals),
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.fixed_key_model(len(proposals)) if fixed_keys else schemas.DocAssignment,
        model = model,
        model_calls = params.MODEL_CALLS,
    )
