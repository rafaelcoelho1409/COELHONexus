"""DeepAgent construction for the YCS multi-hop research planner."""
from __future__ import annotations

import domains
from . import config, params, schemas
from .... import agents, middlewares, prompts

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


def research_planner(model = None):
    """Build the configured tool-free DeepAgent for multi-hop planning."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the YCS research planner requires a named configured model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the YCS research planner requires the configured ChatOpenAI model")

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
        system_prompt = prompts.ask.research_planner.prompts.SYSTEM_PROMPT,
        response_format = schemas.ResearchPlan,
        middleware = [
            ModelCallLimitMiddleware(
                run_limit = params.MAX_MODEL_CALLS, 
                exit_behavior = "end"),
            middlewares.middleware.CapacityWaitMiddleware(),
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
        name = "ycs_ask_research_planner",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict) or set(available_tools) - agents.params.STACK_TOOLS:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the YCS research planner")
    return agent
