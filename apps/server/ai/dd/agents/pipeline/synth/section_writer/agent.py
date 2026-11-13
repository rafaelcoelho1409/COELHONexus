"""DeepAgent that writes one section of a chapter as a cookbook, checked with the validator that judges its answer."""
from __future__ import annotations
import domains
from . import config, params
from .. import schemas
from .... import service
from ..... import agents, middlewares, prompts

from collections.abc import Sequence
from typing import Any

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_openai import ChatOpenAI


def section_writer(*, backend: Any, tools: Sequence[Any], model = None):
    """Build the writer for one section. `backend` mounts the section's code bank on `/vault/`; `tools` hold the section's
    `check_section` (it knows the bank, the valid source keys and the heading). Called once per section: no skill, the rubric is the system prompt."""
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
        raise ValueError("the section writer requires a named OpenAI-compatible Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the section writer must use the configured ChatOpenAI model")

    writer_tools = list(tools)
    tool_names = {tool.name for tool in writer_tools}

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
        tools = writer_tools,
        system_prompt = prompts.pipeline.synth.section_writer.prompts.SYSTEM_PROMPT,
        response_format = schemas.SectionAnswer,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(params.STACK_TOOLS | tool_names),
            ToolCallLimitMiddleware(
                tool_name = "check_section",
                run_limit = params.CHECK_CALL_LIMIT,
                exit_behavior = "continue"),
        ],
        permissions = [
            *[
                FilesystemPermission(
                    operations = ["read"],
                    paths = [path],
                    mode = "allow")
                for path in params.READ_PATHS
            ],
            FilesystemPermission(
                operations = ["read"],
                paths = ["/**"],
                mode = "deny"),
            FilesystemPermission(
                operations = ["write"],
                paths = ["/**"],
                mode = "deny"),
        ],
        backend = backend,
        name = "dd_section_writer",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the section writer"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - agents.params.STACK_TOOLS - tool_names:
        raise RuntimeError(error_message)
    return agent
