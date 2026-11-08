"""DeepAgent that returns a compact, grounded card for one source page."""
from __future__ import annotations
import ai, domains
from . import config, params, schemas
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


def page_card(model = None):
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
        raise ValueError("the page-card agent requires a named OpenAI-compatible Rotator model")
    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(key, HarnessProfile(
        base_system_prompt = "",
        general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
    ))
    agent = create_deep_agent(
        model = model,
        system_prompt = prompts.pipeline.planner.page_card.prompts.SYSTEM_PROMPT,
        response_format = schemas.PageCard,
        middleware = [
            *service.resilience(model_calls = params.MODEL_CALLS, with_tools = False),
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
        name = "dd_page_card",
    )
    tool_node = agent.nodes.get("tools")
    available = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available, dict) or set(available) - ai.dd.agents.params.STACK_TOOLS:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page-card agent")
    return agent
