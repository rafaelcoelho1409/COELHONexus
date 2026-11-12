"""Tool-restricted DeepAgent factory for book knowledge graph candidates."""
from __future__ import annotations

import deepagents
import deepagents.backends
import deepagents.profiles.harness
import langchain.agents.middleware
import langchain_openai

import domains

from .... import middlewares, prompts
from . import config, params, schemas


def graph_extractor(model = None):
    """Create a read-only, no-tools agent with a typed graph extraction response."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the book graph extractor requires a named configured model")
    if not isinstance(model, langchain_openai.ChatOpenAI):
        raise TypeError("the book graph extractor requires the configured chat model")

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
        system_prompt = prompts.rag.graph_extractor.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"})),
            langchain.agents.middleware.ModelCallLimitMiddleware(run_limit = params.MAX_MODEL_CALLS, exit_behavior = "end"),
        ],
        response_format = schemas.GraphExtraction,
        permissions = [
            deepagents.FilesystemPermission(operations = ["read"], paths = ["/skills/**"], mode = "allow"),
            deepagents.FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
            deepagents.FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
        ],
        backend = deepagents.backends.StateBackend(),
        name = "bd_book_graph_extractor",
    )
