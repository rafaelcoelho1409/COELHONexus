"""ycs/grader — async parallel LLM relevance grading.

Imperative Shell — async `gather` orchestration over the structured-
output chain. The LLM (`llm` arg) is constructed by the caller (deprecated
used `with_fallbacks(...)` so a 429 on model #1 transparently routes to
model #2). The fallback chain is wave 4's caller concern, not this
module's.

Direct port of deprecated `services/youtube/grader.py:L22-64`."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.documents import Document

from .params import PER_DOC_CHAR_CAP
from .prompts import GRADING_PROMPT
from .schemas import GradeResult


logger = logging.getLogger(__name__)


class DocumentGrader:
    """Grades document relevance using LLM structured output.

    `llm` is a `RunnableWithFallbacks` (or any Runnable). The default
    `method="json_schema"` (2026-06-11) is the cross-provider portable
    path — sends the schema via `response_format` so providers like
    Groq don't apply their server-side tool-call validator, which
    previously rejected responses whenever a model emitted `"true"`
    instead of boolean `true`. See `rag/standard/nodes/hallucination/
    node.py` for the full rationale."""

    def __init__(self, llm: Any) -> None:
        self.grader = GRADING_PROMPT | llm.with_structured_output(
            GradeResult,
        )

    async def grade_documents(
        self, question: str, documents: list[Document],
    ) -> list[Document]:
        """Grade all documents in parallel. Per-call exceptions are
        captured (not raised) so a single 429 / schema-parse failure
        doesn't tank the whole gather."""
        if not documents:
            return []
        tasks = [
            self.grader.ainvoke({
                "question": question,
                "document": doc.page_content[:PER_DOC_CHAR_CAP],
            })
            for doc in documents
        ]
        results = await asyncio.gather(*tasks, return_exceptions = True)
        kept: list[Document] = []
        for doc, result in zip(documents, results):
            if isinstance(result, Exception):
                logger.info(f"[ycs:grader] failed: {result}")
                continue
            # Defensive: result is a `GradeResult` on success.
            if getattr(result, "score", None) == "relevant":
                kept.append(doc)
        return kept
