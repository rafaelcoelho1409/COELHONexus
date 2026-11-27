"""Typed FastMCP handler for one-shot YCS Ask."""
from __future__ import annotations
from . import params, schemas, service

from typing import Annotated

from pydantic import Field


class YCSAskTools:
    """Expose one citation-aware adaptive YCS Ask turn."""

    async def ask(
        self,
        question: Annotated[
            str, Field(min_length=1, max_length=params.MAX_QUESTION_CHARS)
        ],
        mode: schemas.AskMode | None = None,
        channel_ids: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=params.MAX_CHANNEL_ID_CHARS)]],
            Field(max_length=params.MAX_CHANNEL_IDS),
        ] | None = None,
        sub_questions: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=params.MAX_SUB_QUESTION_CHARS)]],
            Field(max_length=params.MAX_SUB_QUESTIONS),
        ] | None = None,
    ) -> schemas.AskResult:
        """Answer from indexed YCS transcripts and return video citations.

        Args:
            question: Question to answer from indexed YCS evidence.
            mode: Optional route override: fast, standard, or deep.
            channel_ids: Optional channel IDs from the ycs://ask/channels resource.
            sub_questions: Optional research questions used by deep mode.
        """
        return await service.ask(
            question,
            mode=mode,
            channel_ids=channel_ids,
            sub_questions=sub_questions,
        )
