from __future__ import annotations

import asyncio
import logging
import math

from domains.llm.rotator.chain import chat_judge_bandit_async, rerank_via_router_async

from .constants import (
    _JUDGE_BACKOFF_BASE,
    _JUDGE_BODY_CHARS,
    _JUDGE_MAX_ATTEMPTS,
    _JUDGE_MAX_TOKENS,
    _RERANK_BATCH_SIZE,
    _RERANK_DOC_CHARS,
    _RERANK_THRESHOLD,
)


logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Phase A (2026-05-23) — Cross-encoder rerank fast-path
# ════════════════════════════════════════════════════════════════════════════
async def off_topic_via_rerank(
    framework_descriptor: str,
    doc_bodies: list[str],
    *,
    threshold: float = _RERANK_THRESHOLD,
    batch_size: int = _RERANK_BATCH_SIZE,
) -> tuple[list[bool], list[float]]:
    """Batched cross-encoder relevance classification.

    Calls the NIM `nvidia/llama-nemotron-rerank-1b-v2` cross-encoder once per
    batch with `(query=framework_descriptor, passages=docs)`. The model emits
    a logit per (q, p) pair; sigmoid + threshold yields a calibrated KEEP/DROP
    verdict. Replaces ~N parallel LLM-judge calls with ~ceil(N/batch_size) NIM
    rerank calls. Empirically: 280 s → ~15-25 s on 777 docs (12-15× speedup),
    zero LLM parse failures (cross-encoder always returns a number).

    Returns (keep_mask, sigmoid_scores), both in input order. `sigmoid_scores`
    is kept in stats payload so operators can re-tune the threshold from
    historical runs without re-classifying.

    Threshold guidance: 0.35 is the research-recommended starting point. Tune
    on a 50-100 doc hand-labeled validation set per framework family — aim for
    >=95% recall vs the legacy LLM-judge to maintain quality parity.
    """
    n = len(doc_bodies)
    if n == 0:
        return [], []
    keep_mask: list[bool] = [False] * n
    scores: list[float] = [0.0] * n
    # NIM rerank accepts arbitrarily-long passages but performs better when
    # they're truncated to roughly the chunk size used by retrieval. Cap at
    # _RERANK_DOC_CHARS to keep batches under the 8K-token context.
    truncated = [
        (body or "")[:_RERANK_DOC_CHARS] or " "   # NIM 400s on empty input
        for body in doc_bodies
    ]
    for batch_start in range(0, n, batch_size):
        batch_end = min(batch_start + batch_size, n)
        batch = truncated[batch_start:batch_end]
        # rerank_via_router_async returns [(orig_index_within_batch, logit), ...]
        # sorted DESC by logit. We need scores in original input order.
        try:
            pairs = await rerank_via_router_async(
                query=framework_descriptor,
                documents=batch,
                top_n=None,   # want ALL scores, not just top-N
            )
        except Exception as e:
            # Per-batch fail-soft: log + treat batch as all-KEEP (conservative;
            # avoids dropping valid pages on a transient NIM hiccup).
            logger.warning(
                f"[off_topic-rerank] batch [{batch_start}:{batch_end}) failed "
                f"({type(e).__name__}: {e}); marking entire batch as KEEP "
                f"(conservative fail-soft)"
            )
            for i in range(batch_start, batch_end):
                keep_mask[i] = True
                scores[i] = float("nan")
            continue
        # Re-map by orig_index → original_position; apply sigmoid + threshold.
        for orig_idx_in_batch, logit in pairs:
            global_idx = batch_start + int(orig_idx_in_batch)
            try:
                prob = 1.0 / (1.0 + math.exp(-float(logit)))
            except OverflowError:
                prob = 0.0 if float(logit) < 0 else 1.0
            scores[global_idx] = prob
            keep_mask[global_idx] = prob >= threshold
    return keep_mask, scores


def _build_positive_descriptor(entry: dict) -> str:
    """Anchor prompt for the framework. Uses the catalog name + category."""
    name = entry.get("name") or entry.get("slug") or "unknown"
    category = entry.get("category") or ""
    if category:
        return (
            f"Documentation for {name}, a {category} library / framework. "
            f"Teaching content: tutorials, guides, API reference, how-to "
            f"articles, conceptual explanations."
        )
    return (
        f"Documentation for {name}. Teaching content: tutorials, "
        f"guides, API reference, how-to articles, conceptual explanations."
    )


def _build_judge_prompt(framework_name: str, framework_category: str, body: str) -> str:
    """Single-shot KEEP/DROP rubric, designed to be unambiguous so the
    model returns a clean one-word verdict at temperature=0."""
    cat_clause = f", a {framework_category} library/framework" if framework_category else ""
    truncated = (body or "")[:_JUDGE_BODY_CHARS].strip() or "(empty page)"
    return (
        f"You are filtering pages from the official documentation site of "
        f"{framework_name}{cat_clause}.\n\n"
        f"Decide if this page is:\n"
        f"  KEEP → teaching content (tutorials, guides, API reference, "
        f"how-to articles, conceptual explanations of how to use the library)\n"
        f"  DROP → repository meta-content (code of conduct, contributing "
        f"guidelines, sponsor lists, conference talks or event pages, "
        f"blog posts, changelog dumps, release notes, governance policies, "
        f"license text, generated index pages with no real content)\n\n"
        f"Respond with EXACTLY ONE WORD: KEEP or DROP.\n\n"
        f"--- Page content (truncated) ---\n"
        f"{truncated}\n"
        f"--- End page content ---\n\n"
        f"Answer (KEEP or DROP):"
    )


def _parse_verdict(text: str) -> bool | None:
    """Parse the LLM's one-word verdict. Returns True for KEEP, False for
    DROP, None if the response is unparseable (caller decides fallback)."""
    if not text:
        return None
    head = text.strip().upper().split()[0].strip(".,;:!\"'`)")
    if head == "KEEP":
        return True
    if head == "DROP":
        return False
    return None


async def _judge_one(
    sem: asyncio.Semaphore,
    framework_name: str,
    framework_category: str,
    body: str,
    on_complete=None,
) -> tuple[bool, str, str | None, dict]:
    """Run ONE bandit-routed LLM-judge call with cascade fallback.

    Returns (keep, raw_response, error, meta). On final-attempt parse
    failure or all-cascade exception, defaults to KEEP (err on the side
    of preserving content per the user's quality-over-speed rule).

    `meta` carries bandit telemetry: which deployment answered, latency,
    reward — surfaced into stats for operator visibility.

    `on_complete`, when provided, is an async callback invoked once per
    judgment with kwargs (keep: bool, error: str|None). Used by off_topic
    to emit live counter progress."""
    prompt = _build_judge_prompt(framework_name, framework_category, body)
    last_error: str | None = None
    last_response: str = ""
    last_meta: dict = {}
    for attempt in range(_JUDGE_MAX_ATTEMPTS):
        try:
            async with sem:
                response, meta = await chat_judge_bandit_async(
                    prompt,
                    max_tokens=_JUDGE_MAX_TOKENS,
                    temperature=0.0,
                    expected_pattern=r"^(KEEP|DROP)$",
                )
            last_response = response
            last_meta = meta
            verdict = _parse_verdict(response)
            if verdict is not None:
                if on_complete is not None:
                    try:
                        await on_complete(keep=verdict, error=None)
                    except Exception:
                        pass
                return verdict, response, None, meta
            last_error = "unparseable_verdict"
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:160]}"
        if attempt < _JUDGE_MAX_ATTEMPTS - 1:
            await asyncio.sleep(_JUDGE_BACKOFF_BASE ** (attempt + 1))
    if on_complete is not None:
        try:
            await on_complete(keep=True, error=last_error)
        except Exception:
            pass
    return True, last_response, last_error, last_meta
