"""DeepAgent that audits one chapter for cross-chapter consistency, verifying conflicts in the sibling chapters' text."""
from __future__ import annotations
from . import config
from .. import schemas
from .... import entities, service
from ..... import prompts

from typing import Any


STACK_TOOLS = frozenset({"read_file", "grep", "glob"})


def drift_detector(*, backend: Any, model = None) -> entities.Role:
    """Build the detector for one study. `backend` mounts the rendered chapters on `/chapters/`; it reads its skill, greps the siblings and writes nothing."""
    return service.build(
        "dd_drift_detector",
        system_prompt = prompts.pipeline.synth.drift_detector.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = config.ROTATOR_TASK,
        response_format = schemas.ViolationsAnswer,
        skill_role = "drift_detector",
        allowed_tools = STACK_TOOLS,
        backend = backend,
        read_paths = config.READ_PATHS,
        model = model,
        model_calls = config.MODEL_CALLS,
    )
