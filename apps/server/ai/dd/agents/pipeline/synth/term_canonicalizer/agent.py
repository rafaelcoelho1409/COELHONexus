"""DeepAgent that settles the terms chapters define differently."""
from __future__ import annotations
from ...... import dd
import domains
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


def term_canonicalizer(model = None):
    """Build the canonicalizer. Called once per study: system prompt only."""
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
        raise ValueError("the term canonicalizer requires a named OpenAI-compatible Rotator model")

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
        system_prompt = prompts.pipeline.synth.term_canonicalizer.prompts.SYSTEM_PROMPT,
        response_format = schemas.CanonicalAnswer,
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
        name = "dd_term_canonicalizer",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the term canonicalizer"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - dd.agents.params.STACK_TOOLS:
        raise RuntimeError(error_message)
    return agent
