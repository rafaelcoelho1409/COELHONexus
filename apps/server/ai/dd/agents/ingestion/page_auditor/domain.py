from __future__ import annotations

from . import params
from .... import skills


def validate_model_name(model_name: object) -> str:
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the page auditor requires a named OpenAI-compatible Rotator model")
    return model_name


def validate_tool_names(tool_names: set[str]) -> None:
    if tool_names - params.EXCLUDED_TOOLS - skills.params.ALLOWED_TOOL_NAMES:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page auditor")
