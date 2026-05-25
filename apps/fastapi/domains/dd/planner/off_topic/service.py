from __future__ import annotations

import asyncio
import logging

from domains.llm.rotator.chain import chat_judge_bandit_async

from .constants import (
    _JUDGE_BACKOFF_BASE,
    _JUDGE_BODY_CHARS,
    _JUDGE_BODY_MIN_FOR_SPLIT,
    _JUDGE_HEAD_CHARS,
    _JUDGE_HEAD_TAIL_SEP,
    _JUDGE_MAX_ATTEMPTS,
    _JUDGE_MAX_TOKENS,
    _JUDGE_TAIL_CHARS,
)


logger = logging.getLogger(__name__)


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


def _head_tail_truncate(body: str) -> str:
    """Head + tail truncation (Ship 2026-05-25). For pages that fit in
    HEAD+TAIL combined, return the full body. For longer pages, return
    `body[:HEAD] + SEP + body[-TAIL:]` so the judge sees BOTH:

      - leading content (TOC, opening paragraph, role badges) → catches
        meta-content DROP signals like "Code of Conduct", "Sponsors",
        "Contributing Guidelines"
      - trailing content (license blocks, "Edit on GitHub" links,
        changelog footers) → catches the OTHER half of DROP signals
        currently missed by head-only truncation

    Per ICLR 2025 "Lost in the Middle" work, autoregressive LLMs attend
    most to the start AND end of the input — middle content is wasted
    attention for binary classification. Head+tail is the structurally
    correct shape (BERT truncation ablation arXiv 2403.12799 — head+tail
    beats head-only by 1-3 F1 on long-doc classification).
    """
    s = (body or "").strip()
    if not s:
        return "(empty page)"
    if len(s) <= _JUDGE_BODY_MIN_FOR_SPLIT:
        # Fits in combined window — send the WHOLE page, no fake gap.
        return s
    return (
        s[:_JUDGE_HEAD_CHARS]
        + _JUDGE_HEAD_TAIL_SEP
        + s[-_JUDGE_TAIL_CHARS:]
    )


def _build_judge_prompt(framework_name: str, framework_category: str, body: str) -> str:
    """Single-shot KEEP/DROP rubric, designed to be unambiguous so the
    model returns a clean one-word verdict at temperature=0."""
    cat_clause = f", a {framework_category} library/framework" if framework_category else ""
    truncated = _head_tail_truncate(body)
    # Hint the judge about the truncation shape so it doesn't get confused
    # by the "[…]" gap separator on long pages.
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
        f"--- Page content (long pages truncated as `head[…]tail`; "
        f"the `[…]` marker means content was elided between the head "
        f"and tail samples) ---\n"
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
