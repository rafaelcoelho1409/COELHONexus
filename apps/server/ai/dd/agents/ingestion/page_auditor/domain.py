from __future__ import annotations
from . import params, patterns
from .... import prompts


def render_conventions() -> str:
    """Render the export conventions as the bullet list supplied in the auditor's prompt."""
    def inline_code(text: str) -> str:
        return f"`` {text} ``" if "`" in text else f"`{text}`"

    return "\n".join(
        f"- {what}: {', '.join(inline_code(example) for example in examples)}"
        for what, examples in patterns.EXPORT_CONVENTIONS
    )


def system_prompt() -> str:
    """The auditor's prompt with the list of export conventions rendered from the table the audit filter uses."""
    return prompts.ingestion.page_auditor.prompts.SYSTEM_PROMPT.replace(params.CONVENTIONS_MARKER, render_conventions())


def validate_model_name(model_name: object) -> str:
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the page auditor requires a named OpenAI-compatible Rotator model")
    return model_name
