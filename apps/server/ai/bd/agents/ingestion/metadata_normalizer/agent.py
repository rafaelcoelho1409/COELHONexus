"""DeepAgent construction for evidence-grounded book metadata proposals."""
from __future__ import annotations
import domains
from . import config, params
from ... import schemas, service
from .... import middleware, prompts, skills

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import StateBackend
from langchain_openai import ChatOpenAI


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
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the metadata normalizer must use the COELHO Rotator ChatOpenAI model")

    service.isolate_harness_profile(model_name)
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = prompts.ingestion.metadata_normalizer.prompts.SYSTEM_PROMPT,
        middleware = [middleware.service.ToolAllowlistMiddleware(frozenset({"read_file"}))],
        response_format = schemas.BookMetadataProposal,
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
