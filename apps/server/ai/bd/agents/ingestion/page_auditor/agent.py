"""DeepAgent construction for read-only, structured page auditing."""
from __future__ import annotations
import domains
from . import config, domain, params
from ... import schemas
from .... import prompts, skills

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from langchain_openai import ChatOpenAI


def page_auditor(model=None):
    """Build the configured DeepAgent for evidence-grounded page audits."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = domain.validate_model_name(getattr(model, "model_name", None))

    if not isinstance(model, ChatOpenAI):
        raise TypeError("the page auditor must use the COELHO Rotator ChatOpenAI model")

    register_harness_profile(
        f"openai:{model_name}",
        HarnessProfile(
            base_system_prompt = "",
            excluded_tools = params.EXCLUDED_TOOLS,
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.ingestion.page_auditor.prompts.SYSTEM_PROMPT,
        response_format = schemas.PageAssessment,
        permissions = [
            FilesystemPermission(
                operations = ["read"],
                paths = ["/skills/**"],
                mode = "allow",
            ),
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
        skills = [skills.domain.source_for("page_auditor")],
        name = "bd_page_auditor",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict):
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the page auditor")
    domain.validate_tool_names(set(available_tools))
    return agent
