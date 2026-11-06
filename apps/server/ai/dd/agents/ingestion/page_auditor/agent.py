"""DeepAgent construction for read-only, structured documentation page audits."""
from __future__ import annotations
import domains
from . import config, domain, params
from ... import schemas, service
from .... import middleware

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_openai import ChatOpenAI


def page_auditor(model = None):
    """Build the configured DeepAgent for evidence-grounded page audits. It has no tool: the page and the scan leads come in the request,
    the rules are in its prompt, and it answers with a `PageAssessment`."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            timeout_s = config.MODEL.timeout_s,
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = domain.validate_model_name(getattr(model, "model_name", None))

    if not isinstance(model, ChatOpenAI):
        raise TypeError("the page auditor must use the COELHO Rotator ChatOpenAI model")

    service.isolate_harness_profile(model_name)
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = domain.system_prompt(),
        response_format = schemas.PageAssessment,
        permissions = [
            FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
            FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
        ],
        backend = StateBackend(),
        middleware = [
            ModelCallLimitMiddleware(run_limit = params.MODEL_CALLS, exit_behavior = "end"),
            middleware.service.ToolAllowlistMiddleware(frozenset()),
        ],
        name = "dd_page_auditor",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict):
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page auditor")
    domain.validate_tool_names(set(available_tools))
    return agent
