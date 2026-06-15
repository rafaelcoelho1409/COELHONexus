"""ycs/grader — async concurrency-capped LLM relevance grading.

Imperative Shell — async `gather` orchestration over the structured-
output chain, BUT throttled by an `asyncio.Semaphore` (2026-06-15) to
keep the burst pattern compatible with free-tier per-minute rate
windows. See `params.py::GRADER_CONCURRENCY` for the rationale.

The LLM (`llm` arg) is the rotator's `with_fallbacks` chain — a 429 on
deployment #1 transparently rotates to #2. The fallback chain is the
caller's concern, not this module's.

Direct port of deprecated `services/youtube/grader.py:L22-64` +
2026-06-15 concurrency cap."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langchain_core.documents import Document

from .params import GRADER_CONCURRENCY, PER_DOC_CHAR_CAP
from .prompts import GRADING_PROMPT
from .schemas import GradeResult


logger = logging.getLogger(__name__)


def _resolve_concurrency() -> int:
    """Env-overridable concurrency cap. Min 1 so we never deadlock."""
    if "KD_GRADER_CONCURRENCY" in os.environ:
        try:
            return max(1, int(os.environ["KD_GRADER_CONCURRENCY"]))
        except (TypeError, ValueError):
            pass
    return max(1, GRADER_CONCURRENCY)


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
        """Grade documents under a concurrency gate. Per-call exceptions
        are captured (not raised) so a single 429 / schema-parse failure
        doesn't tank the whole batch."""
        if not documents:
            return []
        cap = _resolve_concurrency()
        sem = asyncio.Semaphore(cap)
        logger.debug(
            f"[ycs:grader] grading {len(documents)} doc(s) with "
            f"concurrency cap = {cap}"
        )

        async def _grade_one(doc: Document):
            async with sem:
                return await self.grader.ainvoke({
                    "question": question,
                    "document": doc.page_content[:PER_DOC_CHAR_CAP],
                })

        results = await asyncio.gather(
            *(_grade_one(doc) for doc in documents),
            return_exceptions = True,
        )
        kept: list[Document] = []
        for doc, result in zip(documents, results):
            if isinstance(result, Exception):
                logger.info(f"[ycs:grader] failed: {result}")
                continue
            # Defensive: result is a `GradeResult` on success.
            if getattr(result, "score", None) == "relevant":
                kept.append(doc)
        return kept
