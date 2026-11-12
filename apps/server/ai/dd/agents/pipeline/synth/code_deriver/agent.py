"""DeepAgent that expands a thin documentation reference into a runnable example, checked by a parser tool."""
from __future__ import annotations
from ...... import dd
import domains
from . import config, params
from .. import schemas
from .... import service
from ..... import middlewares, prompts

from collections.abc import Sequence
from typing import Any

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ToolCallLimitMiddleware


def code_deriver(*, tools: Sequence[Any], model = None):
    """Build the deriver. `tools` hold `check_code` (parses the example, checks the length band). One example per thin subtopic: no skill."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            timeout_s = config.MODEL.timeout_s,
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the code deriver requires a named OpenAI-compatible Rotator model")

    deriver_tools = list(tools)
    tool_names = {tool.name for tool in deriver_tools}

    # Deep Agents keys the harness profile by model and registrations are additive: clear this model's key, so nothing registered
    # before (by another role or other code in the process) hides tools from this agent.
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
        tools = deriver_tools,
        system_prompt = prompts.pipeline.synth.code_deriver.prompts.SYSTEM_PROMPT,
        response_format = schemas.DerivedCodeAnswer,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(frozenset(tool_names)),
            ToolCallLimitMiddleware(
                tool_name = "check_code",
                run_limit = params.CHECK_CALL_LIMIT,
                exit_behavior = "continue"),
        ],
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
        name = "dd_code_deriver",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the code deriver"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - dd.agents.params.STACK_TOOLS - tool_names:
        raise RuntimeError(error_message)
    return agent
