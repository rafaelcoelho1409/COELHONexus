"""DeepAgent construction for structured Learning Guide editorial review."""
from __future__ import annotations
import domains
from . import config, entities, params
from .... import middlewares, prompts, skills

from deepagents import (
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


def _build_agent(model: ChatOpenAI, *, name: str, structured_output: bool):
    response_format = (
        ToolStrategy(
            schema = domains.bd.distill.schemas.ReportReviewOutput,
            handle_errors = True,
        )
        if structured_output
        else None
    )
    skill_files = skills.service.files_for("distill_report_reviewer")
    skill_path = f"{skills.domain.source_for('distill_report_reviewer')}SKILL.md"
    skill_data = skill_files.get(skill_path)
    skill_content = skill_data.get("content") if isinstance(skill_data, dict) else None
    if not isinstance(skill_content, str) or not skill_content.strip():
        raise FileNotFoundError("the report reviewer skill has no readable SKILL.md")
    system_prompt = (
        f"{prompts.distill.report_reviewer.prompts.SYSTEM_PROMPT}"
        f"\n\nEditorial review skill:\n{skill_content}"
    )
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = system_prompt,
        middleware = [
            middlewares.middleware.ToolAllowlistMiddleware(
                frozenset({domains.bd.distill.schemas.ReportReviewOutput.__name__})
                if structured_output
                else frozenset(),
            ),
            ModelCallLimitMiddleware(
                run_limit = params.MAX_MODEL_CALLS,
                exit_behavior = "end",
            ),
        ],
        response_format = response_format,
        skills = [],
        permissions = [],
        backend = StateBackend(),
        name = name,
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict):
        raise RuntimeError(f"DeepAgents could not verify tools for {name}")
    unreviewed_tools = set(available_tools) - (
        params.DEFAULT_TOOL_NAMES | skills.params.ALLOWED_TOOL_NAMES
    )
    if unreviewed_tools:
        raise RuntimeError(
            f"DeepAgents exposed an unreviewed tool to {name}: {', '.join(sorted(unreviewed_tools))}",
        )
    return agent


def report_reviewer(model = None) -> entities.ReportReviewer:
    """Build structured and JSON-text DeepAgents over the same Rotator model."""
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
        raise ValueError("the report reviewer requires a named Rotator model")
    if not isinstance(model, ChatOpenAI):
        raise TypeError("the report reviewer must use the COELHO Rotator ChatOpenAI model")

    # Clear this model's harness profile so registrations from other roles cannot alter its tools.
    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    return entities.ReportReviewer(
        structured = _build_agent(
            model,
            name = "bd_report_reviewer",
            structured_output = True,
        ),
        text_fallback = _build_agent(
            model,
            name = "bd_report_reviewer_json_fallback",
            structured_output = False,
        ),
    )
