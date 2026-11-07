"""DeepAgent that audits one chapter for cross-chapter consistency, verifying conflicts in the sibling chapters' text."""
from __future__ import annotations
import ai, domains
from . import config, params
from .. import schemas
from .... import service
from ..... import middlewares, prompts, skills

from typing import Any

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.profiles.harness import harness_profiles


def drift_detector(*, backend: Any, model = None):
    """Build the detector for one study. `backend` mounts the rendered chapters on `/chapters/`; it reads its skill, greps the siblings and writes nothing."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            timeout_s = config.MODEL.timeout_s,
            max_tokens = config.MODEL.max_output_tokens,
            temperature = config.MODEL.temperature,
            max_retries = config.MODEL.max_retries,
            rotator_task = params.ROTATOR_TASK,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("the drift detector requires a named OpenAI-compatible Rotator model")

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
        system_prompt = prompts.pipeline.synth.drift_detector.prompts.SYSTEM_PROMPT,
        response_format = schemas.ViolationsAnswer,
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(params.STACK_TOOLS),
        ],
        permissions = [
            *[
                FilesystemPermission(
                    operations = ["read"],
                    paths = [path],
                    mode = "allow")
                for path in [*params.READ_PATHS, f"{skills.params.VIRTUAL_ROOT}/**"]
            ],
            FilesystemPermission(
                operations = ["read"],
                paths = ["/**"],
                mode = "deny"),
            FilesystemPermission(
                operations = ["write"],
                paths = ["/**"],
                mode = "deny"),
        ],
        backend = backend,
        skills = [skills.domain.source_for("drift_detector")],
        name = "dd_drift_detector",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the drift detector"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - ai.dd.agents.params.STACK_TOOLS:
        raise RuntimeError(error_message)
    return agent
