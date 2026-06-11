"""ycs/rag — PURE helpers shared by both the standard and adaptive graphs.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async, no
clock. Lives at the `rag/` level because `standard/` and `adaptive/`
both call into it."""
from __future__ import annotations

import re
from typing import Any


_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>\s*")


def strip_think_tags(text: Any) -> str:
    """Strip `<think>...</think>` reasoning tokens from model output.

    Accepts either:
      - `str` — the classic shape from chat models.
      - `list` of content blocks — modern LangChain (1.x) returns
        `AIMessage.content` as `list[dict|str]` for thinking-aware
        models (Claude `thinking`, NIM reasoning, GPT-OSS, DeepSeek
        R1, Qwen 3 reasoning). Each block is either `{"type":"text",
        "text": "..."}`, `{"type":"thinking", "thinking": "..."}`,
        or a bare string. Reasoning blocks are DROPPED in line with
        what the `<think>` regex already does for inline tokens.
      - Anything else — coerced via `str()`.

    Why this matters (2026-06-11): the rotator's pool occasionally
    routes to a reasoning model whose response.content is a list,
    which the FAST-path direct_answer node was passing straight into
    `re.sub` → `TypeError: expected string or bytes-like object, got
    'list'`. Centralizing list-handling here covers every existing
    `strip_think_tags(response.content)` call site at once
    (direct_answer, contextualize, rewrite, generate, synthesize).

    Direct port of deprecated `graphs/youtube/helpers.py:L24-27` —
    extended for LangChain 1.x content blocks."""
    if text is None:
        return ""
    if isinstance(text, list):
        parts: list[str] = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                # Drop reasoning blocks outright — same intent as the
                # inline `<think>` regex below.
                if btype in ("thinking", "reasoning"):
                    continue
                # Standard text blocks (OpenAI/Anthropic shape).
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
        text = "".join(parts)
    elif not isinstance(text, str):
        text = str(text)
    return _THINK_TAG_RE.sub("", text).strip()
