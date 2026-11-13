"""DeepAgent construction for choosing and refining Planner v2 chapter groups."""
from __future__ import annotations
import domains
from . import config, params, schemas
from .... import service
from ..... import agents, middlewares, prompts, skills

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.profiles.harness import harness_profiles
from langchain_openai import ChatOpenAI


def chapter_designer(model = None):
    """Build the tool-free DeepAgent with its design skill embedded in the system prompt."""
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
        raise ValueError("the chapter designer requires a named OpenAI-compatible Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the chapter designer must use the configured ChatOpenAI model")

    skill_files = skills.service.files_for("chapter_designer")
    skill = next(
        (file_data.get("content", "") for path, file_data in skill_files.items() if path.endswith("/SKILL.md")),
        "",
    )
    if not skill:
        raise RuntimeError("the chapter-designer skill is missing or empty")
    system_prompt = (
        prompts.pipeline.planner.chapter_designer.prompts.SYSTEM_PROMPT
        + "\n\nChapter-design skill:\n\n"
        + skill
    )

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
        tools = [],
        system_prompt = system_prompt,
        response_format = schemas.ChapterDesignAnswer,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = False),
            middlewares.middleware.ToolAllowlistMiddleware(frozenset()),
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
        name = "dd_chapter_designer",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the chapter designer"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - agents.params.STACK_TOOLS:
        raise RuntimeError(error_message)
    return agent
