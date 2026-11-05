"""DeepAgent construction for bounded, evidence-backed Markdown repairs."""
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


def page_repair(model = None):
    """Build the configured DeepAgent for evidence-bound page repair proposals."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = domain.validate_model_name(getattr(model, "model_name", None))

    if not isinstance(model, ChatOpenAI):
        raise TypeError("the page repair specialist must use the COELHO Rotator ChatOpenAI model")

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
        system_prompt = prompts.ingestion.page_repair.prompts.SYSTEM_PROMPT,
        response_format = schemas.PagePatchBatch,
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
        skills = [skills.domain.source_for("page_repair")],
        name = "dd_page_repair",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the page repair specialist"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    unreviewed_tools = (
        set(available_tools)
        - params.EXCLUDED_TOOLS
        - skills.params.ALLOWED_TOOL_NAMES
    )
    if unreviewed_tools:
        raise RuntimeError(error_message)
    return agent
