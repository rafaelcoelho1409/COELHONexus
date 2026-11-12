"""DeepAgent factory for extracting cited learning units from one bounded source chunk."""
from __future__ import annotations

import deepagents
import deepagents.backends
import deepagents.profiles.harness
import langchain.agents.middleware
import langchain_openai

import domains

from .... import middlewares, prompts
from . import config, params


def learning_extractor(model = None):
    """Build a tool-restricted DeepAgent with a typed extraction response."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the learning extractor requires a named configured model")
    if not isinstance(model, langchain_openai.ChatOpenAI):
        raise TypeError("the learning extractor must use the configured ChatOpenAI model")

    key = f"openai:{model_name}"
    deepagents.profiles.harness.harness_profiles._HARNESS_PROFILES.pop(key, None)
    deepagents.register_harness_profile(
        key,
        deepagents.HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = deepagents.GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    return deepagents.create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.distill.learning_extractor.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"})),
            langchain.agents.middleware.ModelCallLimitMiddleware(run_limit = params.MAX_MODEL_CALLS, exit_behavior = "end"),
        ],
        response_format = domains.bd.distill.schemas.LearningExtraction,
        permissions = [
            deepagents.FilesystemPermission(operations = ["read"], paths = ["/skills/**"], mode = "allow"),
            deepagents.FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
            deepagents.FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
        ],
        backend = deepagents.backends.StateBackend(),
        name = "bd_learning_extractor",
    )
