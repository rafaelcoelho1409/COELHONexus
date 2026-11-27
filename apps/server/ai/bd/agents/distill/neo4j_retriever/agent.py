"""DeepAgent construction for Neo4j-backed Distill retrieval."""
from __future__ import annotations
import domains
from . import config, params
from .... import middlewares, prompts, tools

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


def neo4j_retriever(model = None, *, schema_context: str):
    """Build the configured source-limited Neo4j retrieval DeepAgent."""
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
        raise ValueError("the Neo4j retriever requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the Neo4j retriever must use the configured ChatOpenAI model")

    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    retrieval_tool = tools.neo4j.tools.retrieve_neo4j
    allowed_tools = frozenset({retrieval_tool.name})
    agent = create_deep_agent(
        model = model,
        tools = [retrieval_tool],
        context_schema = tools.retrieval.entities.RetrievalSession,
        system_prompt = prompts.distill.domain.build_retrieval_system_prompt(
            prompts.distill.neo4j_retriever.prompts.SYSTEM_PROMPT,
            schema_context,
        ),
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(allowed_tools),
            ModelCallLimitMiddleware(
                run_limit = params.MAX_MODEL_CALLS,
                exit_behavior = "end",
            ),
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
        name = "bd_distill_neo4j_retriever",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the Distill Neo4j retriever"
    if not isinstance(available_tools, dict) or retrieval_tool.name not in available_tools:
        raise RuntimeError(error_message)
    unreviewed_tools = set(available_tools) - (params.DEFAULT_TOOL_NAMES | allowed_tools)
    if unreviewed_tools:
        raise RuntimeError(f"{error_message}: {', '.join(sorted(unreviewed_tools))}")
    return agent
