"""ycs/rag — PURE helpers shared by both the standard and adaptive graphs.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async, no
clock. Lives at the `rag/` level because `standard/` and `adaptive/`
both call into it."""
from __future__ import annotations

import re


_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>\s*")


def strip_think_tags(text: str) -> str:
    """Strip `<think>...</think>` reasoning tokens from model output.

    Several rotator-routed models (DeepSeek R1, Qwen 3, Magistral) emit
    inner-monologue blocks the deprecated frontend never wanted to
    surface. Direct port of deprecated `graphs/youtube/helpers.py:L24-27`."""
    return _THINK_TAG_RE.sub("", text or "").strip()
