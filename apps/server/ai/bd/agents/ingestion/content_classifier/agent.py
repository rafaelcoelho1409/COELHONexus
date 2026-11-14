"""DeepAgent factory for classifying the learning value of one normalized page."""
from __future__ import annotations
import domains
from . import params
from .... import agents, middlewares, prompts

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


def content_classifier(model = None):
    """Build a read-only agent with a typed content-role result."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = 1200,
            temperature = 0.0,
            max_retries = 2,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the content classifier requires a named configured model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the content classifier must use the configured ChatOpenAI model")

    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.ingestion.content_classifier.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset()),
            ModelCallLimitMiddleware(
                run_limit = 2, 
                exit_behavior = "end"),
        ],
        response_format = agents.schemas.ContentPageAssessment,
        permissions = [
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
        name = "bd_content_page_classifier",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if (
        not isinstance(available_tools, dict)
        or set(available_tools) - params.EXCLUDED_TOOLS - params.INERT_TOOLS
    ):
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the content classifier")
    return agent
