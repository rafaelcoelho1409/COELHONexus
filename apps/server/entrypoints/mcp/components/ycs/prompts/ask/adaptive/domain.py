"""Validate inputs and render the adaptive YCS Ask prompt."""
from __future__ import annotations
from . import prompts


MAX_QUESTION_CHARS = 8_000
MAX_CHANNEL_IDS = 50
MAX_CHANNEL_ID_CHARS = 256
MAX_SUB_QUESTIONS = 5
MAX_SUB_QUESTION_CHARS = 1_000


def build_ask_prompt(
    question: str,
    *,
    mode: str | None = None,
    channel_ids: list[str] | None = None,
    sub_questions: list[str] | None = None,
) -> str:
    question = question.strip()
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise ValueError(
            f"question must contain between 1 and {MAX_QUESTION_CHARS} characters"
        )
    channels = [item.strip() for item in channel_ids or []]
    if len(channels) > MAX_CHANNEL_IDS or any(
        not item or len(item) > MAX_CHANNEL_ID_CHARS for item in channels
    ):
        raise ValueError("channel_ids contains an empty or oversized channel ID")
    questions = [item.strip() for item in sub_questions or []]
    if len(questions) > MAX_SUB_QUESTIONS or any(
        not item or len(item) > MAX_SUB_QUESTION_CHARS for item in questions
    ):
        raise ValueError("sub_questions contains an empty or oversized research question")
    if mode not in (None, "fast", "standard", "deep"):
        raise ValueError("mode must be fast, standard, deep, or omitted")
    return prompts.PROMPT_TEMPLATE.format(
        question=question,
        mode=mode or "automatic",
        channel_ids=", ".join(channels) if channels else "all indexed channels",
        sub_questions="; ".join(questions) if questions else "(let YCS plan)",
    )
