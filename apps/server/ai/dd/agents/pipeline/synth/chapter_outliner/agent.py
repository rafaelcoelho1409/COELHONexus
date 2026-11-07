"""DeepAgent that outlines one chapter of a framework's study book, checked with a validation tool and the chapter's source pages."""
from __future__ import annotations
import ai, domains
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
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ToolCallLimitMiddleware


def chapter_outliner(*, backend: Any, tools: Sequence[Any], model = None):
    """Build the outliner for one chapter. `backend` mounts the chapter's source pages on `/sources/`; `tools` hold the chapter's
    `validate_outline`. It reads its skill and the pages it needs, checks the outline and writes nothing."""
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
        raise ValueError("the chapter outliner requires a named OpenAI-compatible Rotator model")

    outliner_tools = list(tools)
    tool_names = {tool.name for tool in outliner_tools}

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
        tools = outliner_tools,
        system_prompt = prompts.pipeline.synth.chapter_outliner.prompts.SYSTEM_PROMPT,
        response_format = schemas.OutlineAnswer,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(params.STACK_TOOLS | tool_names),
            ToolCallLimitMiddleware(
                tool_name = "validate_outline",
                run_limit = params.CHECK_CALL_LIMIT,
                exit_behavior = "continue"),
        ],
        permissions = [
            *[
                FilesystemPermission(
                    operations = ["read"],
                    paths = [path],
                    mode = "allow")
                for path in [*params.READ_PATHS, f"{skills.params.VIRTUAL_ROOT}/**"]
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
        skills = [skills.domain.source_for("chapter_outliner")],
        name = "dd_chapter_outliner",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the chapter outliner"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - ai.dd.agents.params.STACK_TOOLS - tool_names:
        raise RuntimeError(error_message)
    return agent
