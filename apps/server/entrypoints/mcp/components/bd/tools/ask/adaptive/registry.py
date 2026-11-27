"""FastMCP registration boundary for one-shot BD Ask."""
from __future__ import annotations
from . import params, schemas, tools

from fastmcp import FastMCP
from mcp.types import ToolAnnotations


_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_STATUS_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
)


def register_tools(mcp: FastMCP) -> None:
    ask_handlers = tools.BDAskTools()
    status_handlers = tools.BDPipelineStatusTools()

    @mcp.tool(
        name="bd_ask",
        description="Answer one question from the current indexed BD book and return structured citations.",
        annotations=_READ_ONLY,
        timeout=params.ASK_TIMEOUT_S,
    )
    async def ask(
        book_id: str,
        question: str,
        mode: schemas.AskMode | None = None,
        sub_questions: list[str] | None = None,
    ) -> schemas.AskResult:
        """Run the same adaptive, citation-aware retrieval graph used by BD Ask.

        Args:
            book_id: BD book identifier whose current published index should be searched.
            question: Question to answer from the indexed book.
            mode: Optional route override: fast, standard, or deep.
            sub_questions: Optional research questions for deep mode, with a maximum of four.
        """
        return await ask_handlers.ask(book_id, question, mode, sub_questions)

    @mcp.tool(
        name="bd_book_pipeline_status",
        description="Read one coherent readiness snapshot across source extraction, Ingestion, and current RAG indexing.",
        annotations=_STATUS_READ_ONLY,
        timeout=params.READINESS_TIMEOUT_S,
    )
    async def get_readiness(book_id: str) -> schemas.PipelineReadiness:
        """Read current book readiness, index model compatibility, and active run progress.

        Args:
            book_id: BD book identifier whose current pipeline should be inspected.
        """
        return await status_handlers.get_readiness(book_id)
