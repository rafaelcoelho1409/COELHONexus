"""The tool-free chat model used to choose and refine Planner v2 chapter groups."""
from __future__ import annotations

import domains
from . import config, params


def chapter_designer(model = None):
    """Build the direct Rotator chat model; chapter design has no tools to execute or files to browse."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            timeout_s = config.MODEL.timeout_s,
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the chapter designer requires a named OpenAI-compatible Rotator model")
    if not callable(getattr(model, "with_structured_output", None)):
        raise TypeError("the chapter designer requires a chat model with structured-output support")
    return model
