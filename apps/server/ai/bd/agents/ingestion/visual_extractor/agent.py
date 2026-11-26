"""DeepAgent that transcribes one source figure into a constrained visual schema."""
from __future__ import annotations
import domains
from . import config, params
from ... import schemas
from .... import middlewares, prompts, skills

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
from langchain.agents.structured_output import ToolStrategy
from langchain_openai import ChatOpenAI


def visual_extractor(model = None):
    """Build a vision-capable agent; the configured Rotator selects the vision model for image input."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the visual extractor requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the visual extractor must use the COELHO Rotator ChatOpenAI model")

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
        tools = [],
        system_prompt = prompts.ingestion.visual_extractor.prompts.SYSTEM_PROMPT,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(
                frozenset({"read_file", schemas.VisualExtractionResult.__name__}),
            ),
            ModelCallLimitMiddleware(run_limit = 2, exit_behavior = "end"),
        ],
        response_format = ToolStrategy(
            schema = schemas.VisualExtractionResult,
            handle_errors = True,
        ),
        skills = [skills.domain.source_for("visual_extractor")],
        permissions = [
            FilesystemPermission(
                operations = ["read"], 
                paths = ["/skills/**"], 
                mode = "allow"),
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
        name = "bd_visual_extractor",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict) or set(available_tools) - params.EXCLUDED_TOOLS - params.INERT_TOOLS:
        raise RuntimeError("DeepAgents exposed an unreviewed tool to the visual extractor")
    return agent
