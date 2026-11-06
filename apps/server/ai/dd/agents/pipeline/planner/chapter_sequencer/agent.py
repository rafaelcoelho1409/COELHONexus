"""DeepAgent that orders the chapters of a plan into reading order."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts


def chapter_sequencer(model = None) -> entities.Role:
    """Build the sequencer: one run per ordering sample; it reads its skill and answers with the chapter titles in reading order."""
    return service.build(
        "dd_chapter_sequencer",
        system_prompt = prompts.pipeline.planner.chapter_sequencer.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.ChapterTitleOrder,
        skill_role = "chapter_sequencer",
        allowed_tools = frozenset({"read_file"}),
        model = model,
        model_calls = params.MODEL_CALLS,
    )
