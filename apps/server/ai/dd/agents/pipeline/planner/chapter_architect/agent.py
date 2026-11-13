"""DeepAgent that designs the final chapter outline of a framework's documentation, checked against the corpus with tools."""
from __future__ import annotations
import domains
from . import config, params
from .. import schemas
from .... import service
from ..... import agents, middlewares, prompts, skills

from collections.abc import Sequence
from typing import Any

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_openai import ChatOpenAI


def chapter_architect(*, backend: Any, tools: Sequence[Any], model = None):
    """Build the architect for one run. `backend` mounts the corpus on `/corpus/`; `tools` are the run's scoring tool (the architect
    and its critic share it). It plans with `write_todos`, searches and reads the corpus, delegates the review to `plan_critic`
    and writes nothing."""
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
        raise ValueError("the chapter architect requires a named OpenAI-compatible Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the chapter architect must use the configured ChatOpenAI model")
    architect_tools = list(tools)
    tool_names = {tool.name for tool in architect_tools}
    # The critic sees only the outline it is sent plus the corpus; it carries its own skill and its own resilience stack.
    plan_critic = {
        "name": "plan_critic",
        "description": prompts.pipeline.planner.plan_critic.prompts.DESCRIPTION,
        "system_prompt": prompts.pipeline.planner.plan_critic.prompts.SYSTEM_PROMPT,
        "tools": architect_tools,
        "middleware": [
            *service.resilience(
                model_calls = params.CRITIC_MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(agents.params.READ_TOOLS | tool_names),
        ],
        "skills": [skills.domain.source_for("plan_critic")],
    }

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
        tools = architect_tools,
        system_prompt = prompts.pipeline.planner.chapter_architect.prompts.SYSTEM_PROMPT,
        response_format = schemas.ChapterProposalList,
        subagents = [plan_critic],
        middleware = [
            *service.resilience(
                model_calls = params.MODEL_CALLS,
                with_tools = True),
            middlewares.middleware.ToolAllowlistMiddleware(params.STACK_TOOLS | tool_names),
            ToolCallLimitMiddleware(
                tool_name = "score_outline",
                run_limit = params.SCORE_CALL_LIMIT,
                exit_behavior = "continue"),
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
        skills = [skills.domain.source_for("chapter_architect")],
        name = "dd_chapter_architect",
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    error_message = "DeepAgents exposed an unreviewed tool to the chapter architect"
    if not isinstance(available_tools, dict):
        raise RuntimeError(error_message)
    if set(available_tools) - agents.params.STACK_TOOLS - tool_names:
        raise RuntimeError(error_message)
    return agent
