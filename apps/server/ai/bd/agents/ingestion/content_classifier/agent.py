"""DeepAgent factory for classifying the learning value of one normalized page."""
from __future__ import annotations

import deepagents
import deepagents.backends
import deepagents.profiles.harness
import langchain.agents.middleware
import langchain_openai

import domains

from .... import agents, middlewares, prompts


def content_classifier(model = None):
    """Build a read-only agent with a typed content-role result."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = 1200,
            temperature = 0.0,
            max_retries = 2,
            rotator_task = "bd_content_page_classifier",
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the content classifier requires a named configured model")
    if not isinstance(model, langchain_openai.ChatOpenAI):
        raise TypeError("the content classifier must use the configured ChatOpenAI model")

    key = f"openai:{model_name}"
    deepagents.profiles.harness.harness_profiles._HARNESS_PROFILES.pop(key, None)
    deepagents.register_harness_profile(
        key,
        deepagents.HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = deepagents.GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    agent = deepagents.create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.ingestion.content_classifier.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"})),
            langchain.agents.middleware.ModelCallLimitMiddleware(run_limit = 2, exit_behavior = "end"),
        ],
        response_format = agents.schemas.ContentPageAssessment,
        permissions = [
            deepagents.FilesystemPermission(operations = ["read"], paths = ["/skills/**"], mode = "allow"),
            deepagents.FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
            deepagents.FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
        ],
        backend = deepagents.backends.StateBackend(),
        name = "bd_content_page_classifier",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict) or set(available_tools) - {"read_file"}:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the content classifier")
    return agent
