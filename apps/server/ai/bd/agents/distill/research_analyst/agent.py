"""DeepAgent construction for analysis of bounded Search MCP results."""
from __future__ import annotations

import domains
from . import config, params
from .... import middlewares, prompts, skills, tools

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


def research_analyst(model = None):
    """Build the configured DeepAgent for source-linked current-context findings."""
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
        raise ValueError("the research analyst requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the research analyst must use the COELHO Rotator ChatOpenAI model")

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
    agent_tools = [tools.search_mcp.tool.search_mcp_web]
    allowed_tools = skills.params.ALLOWED_TOOL_NAMES | {item.name for item in agent_tools}
    agent = create_deep_agent(
        model = model,
        tools = agent_tools,
        context_schema = tools.search_mcp.entities.SearchSession,
        system_prompt = prompts.distill.research_analyst.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset(allowed_tools)),
            ModelCallLimitMiddleware(
                run_limit = params.MAX_MODEL_CALLS,
                exit_behavior = "end",
            ),
        ],
        response_format = domains.bd.distill.schemas.ResearchAnalysisOutput,
        skills = [skills.domain.source_for("distill_research_analyst")],
        permissions = [
            FilesystemPermission(
                operations = ["read"],
                paths = ["/skills/**"], 
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
        name = "bd_research_analyst",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the research analyst"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    unreviewed_tools = set(available_tools) - (params.DEFAULT_TOOL_NAMES | allowed_tools)
    if unreviewed_tools:
        raise RuntimeError(f"{error_message}: {', '.join(sorted(unreviewed_tools))}")
    return agent
