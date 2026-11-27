"""Shared adaptive Ask graph construction and input contracts."""

from __future__ import annotations

from typing import Any

import domains

from . import entities


def build_runtime(
    book_id: str,
    run_id: str,
    *,
    counter_run_id: str | None = None,
    model: Any | None = None,
) -> entities.AskRuntime:
    """Build one graph runtime using the current chat endpoint and shared agents."""
    import ai

    if model is None:
        model = domains.settings.chat.service.build_chat_model(timeout_s=600.0)
    context = ai.bd.graphs.ask.adaptive.entities.AskContext(
        book_id=book_id,
        run_id=run_id,
        model=model,
        planner=ai.bd.agents.ask.planner.agent.planner(model),
        grader=ai.bd.agents.ask.grader.agent.grader(model),
        answerer=ai.bd.agents.ask.answer.agent.answerer(model),
        critic=ai.bd.agents.ask.critic.agent.critic(model),
        counter_run_id=counter_run_id,
    )
    return entities.AskRuntime(
        graph=ai.bd.graphs.ask.adaptive.service.adaptive_ask_graph(),
        context=context,
    )


def initial_state(
    *,
    question: str,
    thread_id: str,
    turn_id: str,
    conversation_history: list[dict] | None = None,
    force_mode: str | None = None,
    sub_questions: list[str] | None = None,
) -> dict:
    """Build the state shape shared by streaming HTTP Ask and one-shot MCP Ask."""
    return {
        "question": question,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "conversation_history": conversation_history or [],
        "force_mode": force_mode or "",
        "supplied_sub_questions": sub_questions or [],
        "mode": "",
        "queries": [],
        "documents": [],
        "sub_results": [],
        "citations": [],
        "retry_count": 0,
    }
