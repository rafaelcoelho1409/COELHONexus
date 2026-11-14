"""DeepAgent factory for mapping contents entries to source-page evidence."""
from __future__ import annotations
import domains
from . import params
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
from langchain_openai import ChatOpenAI


def contents_reconciler(model = None):
    """Build a read-only structured agent; application code validates every chosen page candidate."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = 5000,
            temperature = 0.0,
            max_retries = 2,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the contents reconciler requires a named configured model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the contents reconciler must use the configured ChatOpenAI model")

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
        system_prompt = prompts.ingestion.contents_reconciler.prompts.SYSTEM_PROMPT,
        middleware = [middlewares.middleware.ToolAllowlistMiddleware(frozenset())],
        response_format = agents.schemas.ContentsReconciliationProposal,
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
        name = "bd_contents_reconciler",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if (
        not isinstance(available_tools, dict)
        or set(available_tools) - params.EXCLUDED_TOOLS - params.INERT_TOOLS
    ):
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the contents reconciler")
    return agent
