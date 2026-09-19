"""ycs/grader — async concurrency-capped LLM relevance grading.

Imperative Shell — async `gather` orchestration over the structured-
output chain, BUT throttled by an `asyncio.Semaphore` to
keep the burst pattern compatible with free-tier per-minute rate
windows. See `params.py::GRADER_CONCURRENCY` for the rationale.

The LLM (`llm` arg) is the rotator's `with_fallbacks` chain — a 429 on
deployment #1 transparently rotates to #2. The fallback chain is the
caller's concern, not this module's.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import domains
from langchain_core.documents import Document

from . import domain, params, prompts, schemas


logger = logging.getLogger(__name__)


def _resolve_concurrency() -> int:
    """Env-overridable concurrency cap. Min 1 so we never deadlock."""
    if "KD_GRADER_CONCURRENCY" in os.environ:
        try:
            return max(1, int(os.environ["KD_GRADER_CONCURRENCY"]))
        except (TypeError, ValueError):
            pass
    return max(1, params.GRADER_CONCURRENCY)


class DocumentGrader:
    """Grades document relevance using LLM structured output.

    `llm` is a `RunnableWithFallbacks` (or any Runnable). The default
    `method="json_schema"` (2026-06-11) is the cross-provider portable
    path — sends the schema via `response_format` so providers like
    Groq don't apply their server-side tool-call validator, which
    previously rejected responses whenever a model emitted `"true"`
    instead of boolean `true`. See `rag/standard/nodes/hallucination/
    node.py` for the full rationale.

    `include_raw=True` ships the raw `AIMessage` alongside
    the parsed Pydantic so we can rescue the binary intent from the
    payload when the parser dies (see `domain.rescue_score`)."""

    def __init__(self, llm: Any) -> None:
        self.grader = prompts.GRADING_PROMPT | llm.with_structured_output(
            schemas.GradeResult,
            include_raw = True,
        )

    async def grade_documents(
        self, question: str, documents: list[Document],
    ) -> list[Document]:
        """Grade documents under a concurrency gate. Per-call exceptions
        are captured (not raised) so a single 429 / schema-parse failure
        / timeout doesn't tank the whole batch."""
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
                # 2026-09-15: tag every graded doc with the retrieval
                # node so the conversation-level usage counter splits
                # retrieval.grade from retrieval.generate.
                domains.ycs.runtime.llm_counter.service.set_node(node = "grader")
                # Per-call timeout prevents a single slow / hung model
                # from blocking a semaphore slot indefinitely.
                result = await asyncio.wait_for(
                    self.grader.ainvoke({
                        "question": question,
                        "document": doc.page_content[:params.PER_DOC_CHAR_CAP],
                    }),
                    timeout = params.GRADER_CALL_TIMEOUT_S,
                )
                # 2026-09-16: unlike `resilient_ainvoke`'s callers, this
                # bypasses that helper (own semaphore + per-call
                # timeout), so usage capture has to happen here too.
                # `include_raw=True` (see below) returns a dict, not an
                # AIMessage directly — `capture_llm_usage` needs the "raw"
                # half, which carries `.usage_metadata`.
                if isinstance(result, dict):
                    await domains.ycs.rag.service.capture_llm_usage(result.get("raw"))
                return result

        results = await asyncio.gather(
            *(_grade_one(doc) for doc in documents),
            return_exceptions = True,
        )
        kept: list[Document] = []
        rescued = 0
        for doc, result in zip(documents, results):
            if isinstance(result, Exception):
                logger.info(f"[ycs:grader] hard error: {result}")
                continue
            # With `include_raw=True`, success returns a dict
            #   {"raw": AIMessage, "parsed": schemas.GradeResult | None,
            #    "parsing_error": Exception | None}
            # Failure modes:
            #   - parsed is None + parsing_error present → lenient fallback
            #   - parsed.score != "relevant" → drop
            parsed = result.get("parsed") if isinstance(result, dict) else None
            # `domain.KEEPER_SCORES` is the single source of
            # truth for "keep this doc". Both the parsed and rescue
            # paths gate on the same set so the ternary policy can't
            # accidentally diverge between them.
            if parsed is not None and getattr(parsed, "score", None) in domain.KEEPER_SCORES:
                kept.append(doc)
                continue
            if isinstance(result, dict) and parsed is None:
                raw = result.get("raw")
                raw_content = getattr(raw, "content", "") if raw is not None else ""
                score = domain.rescue_score(raw_content or "")
                if score in domain.KEEPER_SCORES:
                    kept.append(doc)
                    rescued += 1
                elif score is None:
                    logger.info(
                        f"[ycs:grader] failed: Invalid json output; "
                        f"raw='{(raw_content or '')[:80]}' "
                        f"parsing_error={result.get('parsing_error')}"
                    )
        if rescued:
            logger.info(
                f"[ycs:grader] rescued {rescued}/{len(documents)} doc(s) "
                f"via raw-payload substring fallback"
            )
        if not kept and documents:
            # 2026-09-15 empty-grade passthrough: every doc dropped
            # (outage hard-errors, or a strict pass on real docs) must
            # NOT yield an empty set — that forces a rewrite round that
            # re-retrieves the same pool and drops it again (observed
            # death spiral). Pass the top reranked docs through instead;
            # the hallucination gate still guards generation quality
            # downstream. Rerank order preserved (best-first).
            fallback_n = min(4, len(documents))
            logger.warning(
                f"[ycs:grader] kept 0/{len(documents)} — passing top "
                f"{fallback_n} reranked doc(s) through instead of an "
                f"empty set"
            )
            return list(documents[:fallback_n])
        return kept
