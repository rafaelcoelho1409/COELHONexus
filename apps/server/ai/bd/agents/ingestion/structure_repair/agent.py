"""DeepAgent construction for evidence-backed structural repair proposals."""
from __future__ import annotations
import domains
from . import config, params
from ... import schemas
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


def structure_repair(model = None):
    """Build the configured DeepAgent for structured page-layout repairs."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the structure-repair specialist requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the structure-repair specialist must use the COELHO Rotator ChatOpenAI model")

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
    repair_tools = [
        tools.patch.tool.check_patches,
        tools.book.tool.adjacent_page_edges,
    ]
    agent = create_deep_agent(
        model = model,
        tools = repair_tools,
        system_prompt = prompts.ingestion.structure_repair.prompts.SYSTEM_PROMPT,
        context_schema = domains.bd.ingestion.ai.entities.SpecialistContext,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file", *(tool.name for tool in repair_tools)}),), 
            ModelCallLimitMiddleware(
                run_limit = domains.bd.ingestion.ai.params.SPECIALIST_MODEL_CALL_LIMIT,
                exit_behavior = "end",
        )],
        response_format = schemas.PatchDraftBatch,
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
        skills = [skills.domain.source_for("structure_repair")],
        name = "bd_structure_repair",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the structure-repair specialist"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    unreviewed_tools = (
        set(available_tools)
        - params.EXCLUDED_TOOLS
        - skills.params.ALLOWED_TOOL_NAMES
        - {tool.name for tool in repair_tools}
    )
    if unreviewed_tools:
        raise RuntimeError(error_message)
    return agent
