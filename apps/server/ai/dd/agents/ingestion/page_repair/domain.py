from __future__ import annotations
from ... import params


def validate_model_name(model_name: object) -> str:
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the page repair specialist requires a named Rotator model")
    return model_name


def validate_tool_names(tool_names: set[str], custom_names: set[str]) -> None:
    if tool_names - params.STACK_TOOLS - custom_names:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page repair specialist")
