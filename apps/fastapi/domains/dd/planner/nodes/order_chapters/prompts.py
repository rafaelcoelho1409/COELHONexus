"""Pedagogical-ordering prompt; domain-agnostic rubric to avoid baking in framework-specific biases."""
from __future__ import annotations

from .params import DESCRIPTION_CHARS


def _normalize_description(desc: str) -> str:
    """Trim + clamp for prompt compactness; the LLM needs only a hint of each chapter."""
    s = (desc or "").strip().replace("\n", " ")
    if len(s) > DESCRIPTION_CHARS:
        return s[:DESCRIPTION_CHARS].rstrip() + "..."
    return s


def build_order_prompt(chapters: list[dict]) -> str:
    """Build the pedagogical-ordering prompt; asks the LLM to return a permutation of chapter indices in prerequisite order."""
    chapter_block = "\n".join(
        f"[{i}] {ch.get('title', '?')!r} — "
        f"{_normalize_description(ch.get('description', ''))}"
        for i, ch in enumerate(chapters)
    )
    n = len(chapters)
    return (
        f"You are a curriculum designer ordering chapters of a technical "
        f"learning book so the reader builds knowledge progressively. The "
        f"chapters are already written; your job is to pick the ORDER "
        f"that maximizes learning efficiency. Apply general pedagogical "
        f"principles:\n\n"
        f"- Installation, setup, CLI/quickstart chapters always come FIRST.\n"
        f"- Foundational concepts before composite/advanced ones.\n"
        f"- Concrete usage before abstract internals.\n"
        f"- A chapter should be readable AFTER all earlier chapters but "
        f"not depend on later ones.\n"
        f"- If two chapters are siblings (no prerequisite relation), "
        f"order them simple-to-complex.\n\n"
        f"CHAPTERS (numbered [0..{n-1}], each shown as title + summary):\n"
        f"{chapter_block}\n\n"
        f'Respond ONLY with valid JSON: {{"order": [<list of all {n} '
        f'chapter indices as integers, each appearing exactly once, in '
        f'pedagogical order>], "rationale": "<1-2 sentences why>"}}\n\n'
        f'Example for 3 chapters: {{"order": [2, 0, 1], "rationale": '
        f'"Chapter 2 introduces installation; chapter 0 is the core API; '
        f'chapter 1 covers advanced patterns."}}'
    )
