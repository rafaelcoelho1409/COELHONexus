"""DeepAgent that designs the final chapter outline of a framework's documentation, checked against the corpus with tools."""
from __future__ import annotations
from . import config, params
from .. import schemas
from .... import entities, service
from ..... import prompts

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware


STACK_TOOLS = frozenset({"write_todos", "ls", "read_file", "glob", "grep", "task"})


def plan_critic(tools: Sequence[Any]) -> dict[str, Any]:
    """The architect's reviewer: a declarative subagent that sees only the outline it is sent plus the corpus, and replies with its defects."""
    return service.subagent(
        "plan_critic",
        description = prompts.pipeline.planner.plan_critic.prompts.DESCRIPTION,
        system_prompt = prompts.pipeline.planner.plan_critic.prompts.SYSTEM_PROMPT,
        skill_role = "plan_critic",
        tools = tools,
        model_calls = params.CRITIC_MODEL_CALLS,
    )


def chapter_architect(*, backend: Any, tools: Sequence[Any], model = None) -> entities.Role:
    """Build the architect for one run. `backend` mounts the corpus on `/corpus/`; `tools` are the run's scoring tool (the architect
    and its critic share it). It plans with `write_todos`, searches and reads the corpus, delegates the review to `plan_critic`
    and writes nothing."""
    return service.build(
        "dd_chapter_architect",
        system_prompt = prompts.pipeline.planner.chapter_architect.prompts.SYSTEM_PROMPT,
        config = config.MODEL,
        rotator_task = params.ROTATOR_TASK,
        response_format = schemas.ChapterProposalList,
        skill_role = "chapter_architect",
        subagent_skill_roles = ("plan_critic",),
        allowed_tools = STACK_TOOLS,
        tools = tools,
        subagents = [plan_critic(tools)],
        backend = backend,
        read_paths = params.READ_PATHS,
        middleware_extra = [
            ToolCallLimitMiddleware(tool_name = "score_outline", run_limit = params.SCORE_CALL_LIMIT, exit_behavior = "continue"),
        ],
        model = model,
        model_calls = params.MODEL_CALLS,
    )
