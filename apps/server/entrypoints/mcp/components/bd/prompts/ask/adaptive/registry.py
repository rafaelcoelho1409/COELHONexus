"""Register the user-selected adaptive BD Ask prompt."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register citation-aware guidance for one-shot BD book questions."""

    @mcp.prompt(
        name="bd_ask_book",
        title="Ask a BD Book",
        description="Guide a citation-aware one-shot question against the current BD book index.",
        tags={"bd", "ask", "rag"},
    )
    def ask_book(book_id: str, question: str) -> str:
        """Prepare a one-shot BD Ask interaction with a requested book question.

        Args:
            book_id: BD book identifier whose current retrieval index should be searched.
            question: Question to answer from indexed book evidence.
        """
        return domain.build_ask_prompt(book_id, question)
