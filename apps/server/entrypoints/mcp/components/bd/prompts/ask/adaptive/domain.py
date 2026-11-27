"""Validate inputs and render the adaptive BD Ask prompt."""
from __future__ import annotations
import domains
from . import prompts


def build_ask_prompt(book_id: str, question: str) -> str:
    """Render a citation-aware one-shot question for a validated BD book."""
    if not domains.bd.identifiers.domain.is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")

    question = question.strip()
    if not question or len(question) > 8000:
        raise ValueError("question must contain between 1 and 8000 characters")

    return prompts.PROMPT_TEMPLATE.format(book_id=book_id, question=question)
