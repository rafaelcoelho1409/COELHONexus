"""The tool-free chat model used for evidence-grounded documentation page audits."""
from __future__ import annotations

import domains
from . import config, domain, params

import langchain_openai


def page_auditor(model = None):
    """Build the named Rotator model; the audit is a bounded schema task with no tools or file access."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            timeout_s = config.MODEL.timeout_s,
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    domain.validate_model_name(getattr(model, "model_name", None))
    if not isinstance(model, langchain_openai.ChatOpenAI):
        raise TypeError("the page auditor must use the COELHO Rotator ChatOpenAI model")
    return model
