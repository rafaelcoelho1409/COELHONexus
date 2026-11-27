"""Register the YCS Ask prompt."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP


AskMode = Literal["fast", "standard", "deep"]


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="ycs_ask_question",
        title="Ask YCS",
        description="Answer a question from indexed YCS video transcripts with citations.",
        tags={"ycs", "ask", "rag"},
    )
    def ask_question(
        question: Annotated[str, Field(min_length=1, max_length=8_000)],
        mode: AskMode | None = None,
        channel_ids: Annotated[list[str], Field(max_length=50)] | None = None,
        sub_questions: Annotated[list[str], Field(max_length=5)] | None = None,
    ) -> str:
        """Prepare one citation-grounded YCS Ask turn."""
        return domain.build_ask_prompt(
            question,
            mode=mode,
            channel_ids=channel_ids,
            sub_questions=sub_questions,
        )
