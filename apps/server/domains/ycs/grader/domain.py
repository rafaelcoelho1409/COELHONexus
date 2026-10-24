"""ycs/grader — PURE helpers (no I/O, no async).

Functional Core (`docs/CODE-CONVENTIONS.md` §4): raw-payload flattening
and lenient-rescue parsing for the relevance grader's structured-output
call. `service.py` orchestrates the actual LLM call; these helpers only
transform already-fetched data."""
from __future__ import annotations

from typing import Any


def flatten_message_content(raw_content: Any) -> str:
    """Flatten an `AIMessage.content` into a string regardless of shape.

    LangChain `BaseMessage.content` can be either:
      - `str`             — plain text (most providers)
      - `list[dict]`      — reasoning models emit
                            `[{type:'thinking',...}, {type:'text', text:'...'},
                             {type:'reasoning',...}]` (kimi-k2, qwen-thinking,
                            deepseek-v4, Claude extended-thinking)

    Bug fix: YCS sub-agents crashed with
    `'list' object has no attribute 'lower'` because `rescue_score`
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


def rescue_score(raw_content: Any) -> str | None:
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
    `flatten_message_content`). Returns one of `"relevant"`,
    `"likely_relevant"`, `"not_relevant"`, or `None` (no signal).

    extended for the ternary grade. Order of checks
    matters: `not_relevant` is the most specific compound token, then
    `likely_relevant`, then bare `relevant`. Checking `relevant`
    first would swallow both compound variants as positives."""
    flat = flatten_message_content(raw_content)
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
