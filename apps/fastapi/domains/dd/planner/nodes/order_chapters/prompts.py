"""order_chapters prompt builder — pedagogical-ordering rubric (domain-
agnostic so the LLM applies general learning principles without baking
in framework-specific biases)."""
from __future__ import annotations

from .params import DESCRIPTION_CHARS


def _normalize_description(desc: str) -> str:
    """Trim + clamp to keep the prompt compact (the LLM only needs a hint
    of what each chapter covers, not the full description)."""
    s = (desc or "").strip().replace("\n", " ")
    if len(s) > DESCRIPTION_CHARS:
        return s[:DESCRIPTION_CHARS].rstrip() + "..."
    return s


def build_order_prompt(chapters: list[dict]) -> str:
    """Build the pedagogical-ordering prompt. Lists chapters by index
    [0, 1, 2, ...] with title + description; asks the LLM to return a
    permutation of indices in pedagogical-prerequisite order."""
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
