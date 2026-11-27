"""DeepAgent construction for source-grounded chapter synthesis."""
from __future__ import annotations
import domains
from . import config, params
from .... import middlewares, prompts, skills

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


def chapter_synthesizer(model = None):
    """Build the configured DeepAgent for a source-grounded chapter learning guide."""
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
        raise ValueError("the chapter synthesizer requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the chapter synthesizer must use the COELHO Rotator ChatOpenAI model")

    # Clear this model's harness profile so registrations from other roles cannot alter its tools.
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
        system_prompt = prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"})),
            ModelCallLimitMiddleware(
                run_limit = params.MAX_MODEL_CALLS,
                exit_behavior = "end",
            ),
        ],
        response_format = domains.bd.distill.ai.schemas.ChapterSynthesis,
        skills = [skills.domain.source_for("distill_chapter_synthesizer")],
        permissions = [
            FilesystemPermission(
                operations = ["read"],
                paths = ["/skills/**"],
                mode = "allow",
            ),
            FilesystemPermission(
                operations = ["read"],
                paths = ["/**"],
                mode = "deny",
            ),
            FilesystemPermission(
                operations = ["write"],
                paths = ["/**"],
                mode = "deny",
            ),
        ],
        backend = StateBackend(),
        name = "bd_chapter_synthesizer",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the chapter synthesizer"
    if not isinstance(available_tools, dict):
        raise TypeError(error_message)
    unreviewed_tools = set(available_tools) - (
        params.DEFAULT_TOOL_NAMES | skills.params.ALLOWED_TOOL_NAMES
    )
    if unreviewed_tools:
        raise RuntimeError(f"{error_message}: {', '.join(sorted(unreviewed_tools))}")
    return agent
