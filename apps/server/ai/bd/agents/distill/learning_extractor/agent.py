"""DeepAgent factory for extracting cited learning units from one bounded source chunk."""
from __future__ import annotations
import domains
from . import config, params
from .... import middlewares, prompts

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_openai import ChatOpenAI


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
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the learning extractor must use the configured ChatOpenAI model")

    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    return create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.distill.learning_extractor.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"})),
            ModelCallLimitMiddleware(run_limit = params.MAX_MODEL_CALLS, exit_behavior = "end"),
        ],
        response_format = domains.bd.distill.schemas.LearningExtraction,
        permissions = [
            FilesystemPermission(
                operations = ["read"],
                paths = ["/skills/**"],
                mode = "allow"),
            FilesystemPermission(
                operations = ["read"],
                paths = ["/**"],
                mode = "deny"),
            FilesystemPermission(
                operations = ["write"],
                paths = ["/**"],
                mode = "deny"),
        ],
        backend = StateBackend(),
        name = "bd_learning_extractor",
    )
