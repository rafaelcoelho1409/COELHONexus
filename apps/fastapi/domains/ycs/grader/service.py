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

from .params import (
    GRADER_CALL_TIMEOUT_S,
    GRADER_CONCURRENCY,
    PER_DOC_CHAR_CAP,
)
from .prompts import GRADING_PROMPT
from .schemas import GradeResult


logger = logging.getLogger(__name__)


# 2026-06-16 — every grade label that counts as "keep the document".
# Currently `relevant` (direct match) and `likely_relevant` (lateral /
# on-topic without literal answer). Promoted to a module-level
# `frozenset` so the keep/drop policy is configurable in one place
# instead of scattered across the parsed-path and rescue-path
# branches. Tightening the policy in the future (drop `likely_relevant`
# again for a high-precision query class) is one edit here.
_KEEPER_SCORES: frozenset[str] = frozenset(("relevant", "likely_relevant"))


def _resolve_concurrency() -> int:
    """Env-overridable concurrency cap. Min 1 so we never deadlock."""
    if "KD_GRADER_CONCURRENCY" in os.environ:
        try:
            return max(1, int(os.environ["KD_GRADER_CONCURRENCY"]))
        except (TypeError, ValueError):
            pass
    return max(1, GRADER_CONCURRENCY)


def _flatten_message_content(raw_content: Any) -> str:
    """Flatten an `AIMessage.content` into a string regardless of shape.

    LangChain `BaseMessage.content` can be either:
      - `str`             — plain text (most providers)
      - `list[dict]`      — reasoning models emit
                            `[{type:'thinking',...}, {type:'text', text:'...'},
                             {type:'reasoning',...}]` (kimi-k2, qwen-thinking,
                            deepseek-v4, Claude extended-thinking)

    2026-06-15 bug: YCS sub-agents crashed with
    `'list' object has no attribute 'lower'` because `_rescue_score`
    received a list-shaped content from a reasoning model. The rotator's
    `_flatten_thinking_content` only sanitizes INCOMING messages (next
    cascade arm safety); the model's OUTGOING response can still be a
    list. This helper closes the gap on the consumer side.

    Returns "" on any non-str/non-list input (defensive)."""
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        texts: list[str] = []
        for block in raw_content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    texts.append(t)
            elif isinstance(block, str) and block:
                texts.append(block)
        return "\n".join(texts)
    return ""


def _rescue_score(raw_content: Any) -> str | None:
    """Lenient fallback when Pydantic structured-output parse fails.

    Free-tier rotator pool occasionally yields models that emit
    truncated / malformed JSON envelopes (`not_relevant"}` was the 2026-
    06-15 production crash trigger). When parsing dies, the binary
    intent is almost always still recoverable from the raw payload —
    every grading model phrases its verdict as some variant of
    `relevant` / `likely_relevant` / `not_relevant` / `irrelevant`.
    Surfacing the intent here avoids the catastrophic loop where the
    standard sub-graph drops every doc, retries `rewrite → retrieve`,
    and burns the sub-agent's recursion budget on what was actually a
    parse hiccup.

    Accepts `str` OR reasoning-model `list[dict]` content (via
    `_flatten_message_content`). Returns one of `"relevant"`,
    `"likely_relevant"`, `"not_relevant"`, or `None` (no signal).

    2026-06-16 — extended for the ternary grade. Order of checks
    matters: `not_relevant` is the most specific compound token, then
    `likely_relevant`, then bare `relevant`. Checking `relevant`
    first would swallow both compound variants as positives."""
    flat = _flatten_message_content(raw_content)
    if not flat:
        return None
    text = flat.lower()
    if "not_relevant" in text or "not relevant" in text or "irrelevant" in text:
        return "not_relevant"
    if "likely_relevant" in text or "likely relevant" in text or "partially relevant" in text:
        return "likely_relevant"
    if "relevant" in text:
        return "relevant"
    return None


class DocumentGrader:
    """Grades document relevance using LLM structured output.

    `llm` is a `RunnableWithFallbacks` (or any Runnable). The default
    `method="json_schema"` (2026-06-11) is the cross-provider portable
    path — sends the schema via `response_format` so providers like
    Groq don't apply their server-side tool-call validator, which
    previously rejected responses whenever a model emitted `"true"`
    instead of boolean `true`. See `rag/standard/nodes/hallucination/
    node.py` for the full rationale.

    2026-06-15 — `include_raw=True` ships the raw `AIMessage` alongside
    the parsed Pydantic so we can rescue the binary intent from the
    payload when the parser dies (see `_rescue_score`)."""

    def __init__(self, llm: Any) -> None:
        self.grader = GRADING_PROMPT | llm.with_structured_output(
            GradeResult,
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
                # Per-call timeout prevents a single slow / hung model
                # from blocking a semaphore slot indefinitely.
                return await asyncio.wait_for(
                    self.grader.ainvoke({
                        "question": question,
                        "document": doc.page_content[:PER_DOC_CHAR_CAP],
                    }),
                    timeout = GRADER_CALL_TIMEOUT_S,
                )

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
            #   {"raw": AIMessage, "parsed": GradeResult | None,
            #    "parsing_error": Exception | None}
            # Failure modes:
            #   - parsed is None + parsing_error present → lenient fallback
            #   - parsed.score != "relevant" → drop
            parsed = result.get("parsed") if isinstance(result, dict) else None
            # 2026-06-16 — `_KEEPER_SCORES` is the single source of
            # truth for "keep this doc". Both the parsed and rescue
            # paths gate on the same set so the ternary policy can't
            # accidentally diverge between them.
            if parsed is not None and getattr(parsed, "score", None) in _KEEPER_SCORES:
                kept.append(doc)
                continue
            # Lenient fallback: rescue intent from the raw payload.
            if isinstance(result, dict) and parsed is None:
                raw = result.get("raw")
                raw_content = getattr(raw, "content", "") if raw is not None else ""
                score = _rescue_score(raw_content or "")
                if score in _KEEPER_SCORES:
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
        return kept
