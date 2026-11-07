"""DeepAgent that splits one oversize chapter, checking its split with tools."""
from __future__ import annotations
import ai
import domains
from . import config, params
from .. import schemas
from .... import service
from ..... import middlewares, prompts, skills

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


def chapter_curator(*, tools: Sequence[Any], model = None):
    """Build the curator for one chapter. `tools` are that chapter's `cluster_units` and `validate_split`, bound to its units."""
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
        raise ValueError("the chapter curator requires a named OpenAI-compatible Rotator model")

    curator_tools = list(tools)
    tool_names = {tool.name for tool in curator_tools}

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
        tools = curator_tools,
        system_prompt = prompts.pipeline.planner.chapter_curator.prompts.SYSTEM_PROMPT,
        response_format = schemas.SplitPlan,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(params.STACK_TOOLS | tool_names),
        ],
        permissions = [
            FilesystemPermission(
                operations = ["read"],
                paths = [f"{skills.params.VIRTUAL_ROOT}/**"],
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
        skills = [skills.domain.source_for("chapter_curator")],
        name = "dd_chapter_curator",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the chapter curator"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - ai.dd.agents.params.STACK_TOOLS - tool_names:
        raise RuntimeError(error_message)
    return agent
