"""DeepAgent construction for minimal, evidence-backed text repairs."""
from __future__ import annotations
import domains
from . import config, params
from ... import schemas
from .... import prompts

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from langchain_openai import ChatOpenAI


def text_repair(model = None):
    """Build the configured DeepAgent for structured text repair proposals."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the text-repair specialist requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the text-repair specialist must use the COELHO Rotator ChatOpenAI model")

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
        system_prompt = prompts.ingestion.text_repair.prompts.SYSTEM_PROMPT,
        response_format = schemas.PatchProposalBatch,
        permissions = [FilesystemPermission(
            operations = ["read", "write"],
            paths = ["/**"],
            mode = "deny",
        )],
        backend = StateBackend(),
        name = "bd_text_repair",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict) or set(available_tools) - params.EXCLUDED_TOOLS:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the text-repair specialist")
    return agent
