"""DeepAgent construction for read-only, structured page auditing."""
from __future__ import annotations
import domains
from . import config, domain, params
from ... import schemas
from .... import middlewares, prompts, skills

import deepagents
import deepagents.backends
import deepagents.profiles.harness
import langchain.agents.middleware
import langchain_openai


def page_auditor(model = None):
    """Build the configured DeepAgent for evidence-grounded page audits."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = domain.validate_model_name(getattr(model, "model_name", None))

    if not isinstance(model, langchain_openai.ChatOpenAI):
        raise TypeError("the page auditor must use the COELHO Rotator ChatOpenAI model")

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
        tools = [],
        system_prompt = prompts.ingestion.page_auditor.prompts.SYSTEM_PROMPT,
        middleware = [middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"})), langchain.agents.middleware.ModelCallLimitMiddleware(
            run_limit = domains.bd.ingestion.ai.params.AGENT_MODEL_CALL_LIMIT,
            exit_behavior = "end",
        )],
        response_format = schemas.PageAuditResponse,
        permissions = [
            deepagents.FilesystemPermission(
                operations = ["read"],
                paths = ["/skills/**"],
                mode = "allow",
            ),
            deepagents.FilesystemPermission(
                operations = ["read"],
                paths = ["/**"],
                mode = "deny",
            ),
            deepagents.FilesystemPermission(
                operations = ["write"],
                paths = ["/**"],
                mode = "deny",
            ),
        ],
        backend = deepagents.backends.StateBackend(),
        skills = [skills.domain.source_for("page_auditor")],
        name = "bd_page_auditor",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict):
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page auditor")
    domain.validate_tool_names(set(available_tools))
    return agent
