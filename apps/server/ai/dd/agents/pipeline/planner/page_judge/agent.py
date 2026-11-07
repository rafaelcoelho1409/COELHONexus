"""DeepAgent that returns the KEEP/DROP verdict of one documentation page."""
from __future__ import annotations
import ai, domains
from . import config, params
from .. import schemas
from .... import service
from ..... import middlewares, prompts

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.profiles.harness import harness_profiles


def page_judge(model = None):
    """Build the judge. Its whole rubric is its system prompt (no skill, no tools): it is called once per page, hundreds of times a run."""
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
        raise ValueError("the page judge requires a named OpenAI-compatible Rotator model")

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
        system_prompt = prompts.pipeline.planner.page_judge.prompts.SYSTEM_PROMPT,
        response_format = schemas.PageRelevance,
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
        name = "dd_page_judge",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the page judge"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - ai.dd.agents.params.STACK_TOOLS:
        raise RuntimeError(error_message)
    return agent
