"""Register the one-shot adaptive YCS Ask tool."""
from __future__ import annotations
from . import params, schemas, tools

from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


def register_tools(mcp: FastMCP) -> None:
    handlers = tools.YCSAskTools()

    @mcp.tool(
        name="ycs_ask",
        description="Answer one question from indexed YCS video content and return citations; optionally scope by channel and adaptive mode.",
        annotations=_READ_ONLY,
        timeout=params.ASK_TIMEOUT_S,
    )
    async def ask(
        question: Annotated[str, Field(min_length=1, max_length=params.MAX_QUESTION_CHARS)],
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
        """Run one YCS adaptive RAG turn without creating conversation history.

        Args:
            question: Question to answer from indexed YCS evidence.
            mode: Optional route override: fast, standard, or deep.
            channel_ids: Optional channel IDs from ycs://ask/channels.
            sub_questions: Optional research questions used by deep mode.
        """
        return await handlers.ask(question, mode, channel_ids, sub_questions)
