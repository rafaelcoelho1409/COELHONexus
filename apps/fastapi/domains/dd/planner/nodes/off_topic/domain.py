"""off_topic pure helpers (verdict parser); prompt strings + head+tail prep in prompts.py."""
from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_VERDICT_RE = re.compile(r"\b(KEEP|DROP)\b", re.IGNORECASE)


def parse_verdict(text: str) -> bool | None:
    """Parse the LLM's KEEP/DROP verdict. Returns True for KEEP, False for
    DROP, None if unparseable (caller decides fallback).

    Reasoning-tuned models in the rotator's general pool (gpt-oss, deepseek-v4,
    etc.) prepend a <think>...</think> block before the verdict — strip it
    first so a stray "keep"/"drop" mentioned mid-reasoning isn't mistaken for
    the answer. What's left is searched for KEEP/DROP anywhere (not just an
    exact first word — handles markdown wrapping like **KEEP** or a verdict
    embedded in a sentence), taking the LAST match, since a model that restates
    its conclusion states the real answer last.
    """
    if not text:
        return None
    lowered = text.lower()
    if "<think>" in lowered and "</think>" not in lowered:
        # Reasoning got cut off mid-thought (hit max_tokens before closing) —
        # nothing past this point is a real conclusion, don't scan it for a
        # stray "keep"/"drop" mentioned in passing while still reasoning.
        return None
    body = _THINK_BLOCK_RE.sub("", text).strip()
    if not body:
        body = text.strip()
    matches = _VERDICT_RE.findall(body)
    if not matches:
        return None
    return matches[-1].upper() == "KEEP"
