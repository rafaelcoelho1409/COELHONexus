from __future__ import annotations

from . import patterns
from ... import params as agent_params
from .... import prompts


CONVENTIONS_MARKER = "<<EXPORT_CONVENTIONS>>"


def system_prompt() -> str:
    """The auditor's prompt with the list of export conventions rendered from the table the audit filter uses."""
    return prompts.ingestion.page_auditor.prompts.SYSTEM_PROMPT.replace(CONVENTIONS_MARKER, patterns.render_conventions())


def validate_model_name(model_name: object) -> str:
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the page auditor requires a named OpenAI-compatible Rotator model")
    return model_name


def validate_tool_names(tool_names: set[str]) -> None:
    if tool_names - agent_params.STACK_TOOLS:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page auditor")
