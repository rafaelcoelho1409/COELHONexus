"""DeepAgent construction for evidence-grounded book metadata proposals."""
from __future__ import annotations
import domains
from . import config, params
from ... import schemas
from .... import middlewares, prompts, skills

import deepagents
import deepagents.backends
import deepagents.profiles.harness
import langchain_openai


def metadata_normalizer(model = None):
    """Build the configured DeepAgent for normalized title and author proposals."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the metadata normalizer requires a named Rotator model")
    if not isinstance(model, langchain_openai.ChatOpenAI):
        raise TypeError("the metadata normalizer must use the COELHO Rotator ChatOpenAI model")

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
        system_prompt = prompts.ingestion.metadata_normalizer.prompts.SYSTEM_PROMPT,
        middleware = [middlewares.middleware.ToolAllowlistMiddleware(frozenset({"read_file"}))],
        response_format = schemas.BookMetadataProposal,
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
        skills = [skills.domain.source_for("metadata_normalizer")],
        name = "bd_metadata_normalizer",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the metadata normalizer"
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
