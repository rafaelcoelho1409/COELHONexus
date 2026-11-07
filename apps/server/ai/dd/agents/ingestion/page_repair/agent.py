"""DeepAgent construction for bounded, evidence-backed Markdown repairs."""
from __future__ import annotations
import domains
from . import config, domain, params
from ... import schemas
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
from langchain.agents.middleware import (
    ModelCallLimitMiddleware, 
    ToolCallLimitMiddleware, 
    ToolErrorMiddleware
)
from langchain_openai import ChatOpenAI


def page_repair(*, check: tools.ingestion.check_patch.entities.Check, model = None):
    """Build the configured DeepAgent for evidence-bound page repair proposals. Its one tool, `check_patch`, runs `check` (the
    application's own patch rules) on a patch before the agent answers; the application still applies the same rules to the answer."""
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
        raise TypeError("the page repair specialist must use the COELHO Rotator ChatOpenAI model")

    check_patch = tools.ingestion.check_patch.tool.check_patch_tool(check)
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
        tools = [check_patch],
        system_prompt = prompts.ingestion.page_repair.prompts.SYSTEM_PROMPT,
        response_format = schemas.PagePatchBatch,
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
        middleware = [
            ModelCallLimitMiddleware(
                run_limit = params.MODEL_CALLS, 
                exit_behavior = "end"),
            ToolCallLimitMiddleware(
                tool_name = check_patch.name, 
                run_limit = params.CHECK_CALLS, 
                exit_behavior = "continue"),
            ToolErrorMiddleware(lambda error, request: f"`{request.tool_call['name']}` failed with {type(error).__name__}: {str(error)[:300]}"),
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({check_patch.name})),
        ],
        name = "dd_page_repair",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict):
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page repair specialist")
    domain.validate_tool_names(set(available_tools), {check_patch.name})
    return agent
