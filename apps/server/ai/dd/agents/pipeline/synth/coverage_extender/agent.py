"""DeepAgent that adds subtopics for the code blocks a written section left unused."""
from __future__ import annotations
from ...... import dd
import domains
from . import config, params
from .. import schemas
from .... import service
from ..... import middlewares, prompts

import deepagents
import deepagents.backends
import deepagents.profiles.harness


def coverage_extender(model = None):
    """Build the extender. Its rubric is its system prompt (no skill, no tools): the node validates what it proposes (`parse_topup`)."""
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
        raise ValueError("the coverage extender requires a named OpenAI-compatible Rotator model")

    # Deep Agents keys the harness profile by model and registrations are additive: clear this model's key, so nothing registered
    # before (by another role or other code in the process) hides tools from this agent.
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
        system_prompt = prompts.pipeline.synth.coverage_extender.prompts.SYSTEM_PROMPT,
        response_format = schemas.ExtensionAnswer,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = False),
            middlewares.middleware.ToolAllowlistMiddleware(frozenset()),
        ],
        permissions = [
            deepagents.FilesystemPermission(
                operations = ["read"],
                paths = ["/**"],
                mode = "deny"),
            deepagents.FilesystemPermission(
                operations = ["write"],
                paths = ["/**"],
                mode = "deny"),
        ],
        backend = deepagents.backends.StateBackend(),
        name = "dd_coverage_extender",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the coverage extender"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - dd.agents.params.STACK_TOOLS:
        raise RuntimeError(error_message)
    return agent
