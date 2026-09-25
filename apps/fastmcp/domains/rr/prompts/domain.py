"""RR prompts — pure build/validate helpers (Functional Core).

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no logging. Same
inputs → same outputs. The `@mcp.prompt` boundary in `__init__.py` is a
one-call shell over these functions.
"""
from __future__ import annotations
from . import prompts


def build_digest_today_prompt(topic: str, verticals: str, top_n: int) -> str:
    """Render the digest_today scan request.

    Blank topic/verticals fall back to defaults; top_n clamps to 4-30
    (triage tolerates fewer, deep_read fans out too wide above).
    """
    return prompts.DIGEST_TODAY_TEMPLATE.format(
        topic     = topic.strip() or "deep agents",
        verticals = verticals.strip() or "cs.LG, cs.AI",
        top_n     = max(4, min(30, int(top_n))),
    )
