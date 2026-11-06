"""DeepAgent construction for the second reader of judgement-call page findings."""
from __future__ import annotations
import domains
from . import config, params
from ... import schemas, service
from .... import middleware, prompts

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_openai import ChatOpenAI


def finding_verifier(model = None):
    """Build the configured DeepAgent that confirms or dismisses a few auditor findings."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the finding verifier requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the finding verifier must use the COELHO Rotator ChatOpenAI model")

    service.isolate_harness_profile(model_name)
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.ingestion.finding_verifier.prompts.SYSTEM_PROMPT,
        middleware = [middleware.service.ToolAllowlistMiddleware(frozenset()), ModelCallLimitMiddleware(
            run_limit = domains.bd.ingestion.ai.params.AGENT_MODEL_CALL_LIMIT,
            exit_behavior = "end",
        )],
        response_format = schemas.FindingCheckBatch,
        permissions = [
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
        name = "bd_finding_verifier",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the finding verifier"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - params.EXCLUDED_TOOLS - params.INERT_TOOLS:
        raise RuntimeError(error_message)
    return agent
