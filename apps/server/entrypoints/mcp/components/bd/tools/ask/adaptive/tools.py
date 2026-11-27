"""FastMCP handler methods for one-shot BD Ask."""
from __future__ import annotations
from . import params, schemas, service

from typing import Annotated

from pydantic import Field


class BDAskTools:
    """Expose one citation-aware adaptive Ask turn without creating a chat thread."""

    async def ask(
        self,
        book_id: str,
        question: Annotated[
            str, Field(min_length=1, max_length=params.MAX_QUESTION_CHARS)
        ],
        mode: schemas.AskMode | None = None,
        sub_questions: Annotated[list[str], Field(max_length=params.MAX_SUB_QUESTIONS)]
        | None = None,
    ) -> schemas.AskResult:
        """Answer one question from the current indexed book and return linked citations.

        Args:
            book_id: BD book identifier whose published retrieval index should be used.
            question: Question to answer from the book's indexed evidence.
            mode: Optional adaptive route override: fast, standard, or deep.
            sub_questions: Optional bounded research questions used by deep mode.
        """
        return await service.ask(
            book_id, question, mode=mode, sub_questions=sub_questions
        )


class BDPipelineStatusTools:
    """Expose the current book readiness snapshot through a read-only tool."""

    async def get_readiness(self, book_id: str) -> schemas.PipelineReadiness:
        """Return readiness and embedding compatibility for one BD book."""
        return schemas.PipelineReadiness.model_validate(
            await service.read_readiness(book_id)
        )
