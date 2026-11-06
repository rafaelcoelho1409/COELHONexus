"""DeepAgent that proposes the chapters one documentation section supports."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts


def section_analyst(model = None) -> entities.Role:
    """Build the analyst: one run per section, in parallel; it reads its skill and answers from the summaries it is sent."""
    return service.build(
        "dd_section_analyst",
        system_prompt = prompts.pipeline.planner.section_analyst.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.SectionProposals,
        skill_role = "section_analyst",
        allowed_tools = frozenset({"read_file"}),
        model = model,
        model_calls = params.MODEL_CALLS,
    )
