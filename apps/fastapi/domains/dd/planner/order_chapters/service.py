"""order_chapters service — prompt builder, Borda aggregator, parsing."""
from __future__ import annotations

import asyncio
import json

from domains.llm.rotator.chain import chat_judge_bandit_async

from .constants import (
    _BLOB_PREFIX,
    _DESCRIPTION_CHARS,
    _FOUNDATIONAL_RE,
    _JSON_RE,
    _MAX_TOKENS,
    _TEMPERATURE,
)


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/order/{manifest_hash}.json"


def is_foundational(title: str) -> bool:
    """True if chapter title looks like installation/setup/cli/quickstart —
    foundational content that MUST anchor at position 0 regardless of what
    the LLM proposes."""
    if not title:
        return False
    return bool(_FOUNDATIONAL_RE.search(title))


def _normalize_description(desc: str) -> str:
    """Trim + clamp to keep the prompt compact (the LLM only needs a hint
    of what each chapter covers, not the full description)."""
    s = (desc or "").strip().replace("\n", " ")
    if len(s) > _DESCRIPTION_CHARS:
        return s[:_DESCRIPTION_CHARS].rstrip() + "..."
    return s


def build_order_prompt(chapters: list[dict]) -> str:
    """Build the pedagogical-ordering prompt. Lists chapters by index
    [0, 1, 2, ...] with title + description; asks the LLM to return a
    permutation of indices in pedagogical-prerequisite order.

    The prompt is intentionally domain-agnostic — the LLM applies general
    learning principles (foundations before applications, concrete before
    abstract, simple before complex) without baking in framework-specific
    biases.
    """
    chapter_block = "\n".join(
        f"[{i}] {ch.get('title', '?')!r} — "
        f"{_normalize_description(ch.get('description', ''))}"
        for i, ch in enumerate(chapters)
    )
    n = len(chapters)
    return (
        f"You are a curriculum designer ordering chapters of a technical "
        f"learning book so the reader builds knowledge progressively. The "
        f"chapters are already written; your job is to pick the ORDER that "
        f"maximizes learning efficiency. Apply general pedagogical "
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


def parse_order_response(text: str, n_chapters: int) -> list[int] | None:
    """Parse the LLM's order response. Returns a list of chapter indices
    (length n_chapters, each index appearing exactly once) OR None if the
    response is invalid / incomplete.

    Strict validation: every index must be in range and unique — any
    duplication or out-of-range index → None (caller drops that sample).
    """
    if not text:
        return None
    try:
        parsed = json.loads(text.strip())
    except Exception:
        m = _JSON_RE.search(text)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return None
    if isinstance(parsed, dict):
        order_raw = parsed.get("order")
    elif isinstance(parsed, list):
        order_raw = parsed
    else:
        return None
    if not isinstance(order_raw, list):
        return None
    try:
        order = [int(x) for x in order_raw]
    except (ValueError, TypeError):
        return None
    if len(order) != n_chapters:
        return None
    if sorted(order) != list(range(n_chapters)):
        return None
    return order


def borda_aggregate(rankings: list[list[int]], n_chapters: int) -> list[int]:
    """Borda count aggregation across multiple rank-orderings.

    Each ranking is a permutation of [0, n-1]. For each chapter, we sum
    (n - position - 1) points across all rankings — first place gets n-1,
    last place gets 0. The final ordering is by descending Borda score.

    Tie-breaking: stable — the chapter that appeared first in EARLIER
    rankings wins ties (preserves the bandit's primary-deployment opinion
    when samples agree).

    Empty rankings degrade gracefully to the identity permutation.
    """
    if not rankings:
        return list(range(n_chapters))
    scores = [0] * n_chapters
    first_appearance = [n_chapters] * n_chapters
    for r_idx, ranking in enumerate(rankings):
        for pos, chapter_idx in enumerate(ranking):
            if 0 <= chapter_idx < n_chapters:
                scores[chapter_idx] += (n_chapters - pos - 1)
                if r_idx < first_appearance[chapter_idx]:
                    first_appearance[chapter_idx] = r_idx
    # Sort by (-score, first_appearance) for descending Borda + stable tie-break.
    return sorted(
        range(n_chapters),
        key=lambda i: (-scores[i], first_appearance[i]),
    )


def apply_foundational_prefix_rule(
    order: list[int],
    chapters: list[dict],
) -> tuple[list[int], list[int]]:
    """Pin chapters matching foundational keywords to the FRONT of the order.

    If multiple chapters match (e.g., "Installation" AND "Getting Started"),
    they all anchor at the front, preserving their relative pedagogical
    order from the Borda-aggregated ranking.

    Returns (new_order, foundational_indices_promoted) for telemetry.
    """
    foundational_indices = [
        i for i, ch in enumerate(chapters)
        if is_foundational(ch.get("title", ""))
    ]
    if not foundational_indices:
        return list(order), []
    foundational_set = set(foundational_indices)
    # Foundational chapters in their Borda-aggregated relative order
    fnd_in_order = [i for i in order if i in foundational_set]
    rest = [i for i in order if i not in foundational_set]
    return fnd_in_order + rest, fnd_in_order


async def _sample_one_ordering(
    sem: asyncio.Semaphore,
    prompt: str,
    n_chapters: int,
) -> tuple[list[int] | None, dict]:
    """One LLM call. Returns (parsed_order_or_None, meta)."""
    async with sem:
        try:
            response, meta = await chat_judge_bandit_async(
                prompt, max_tokens=_MAX_TOKENS, temperature=_TEMPERATURE,
            )
        except Exception as e:
            return None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    order = parse_order_response(response, n_chapters)
    if order is None:
        return None, {**meta, "error": "parse_failed",
                      "raw": (response or "")[:120]}
    return order, meta


def load_chapter_order(text: str) -> list[int] | None:
    """Convenience loader for plan_write. Returns the ordered list of chapter
    indices, or None if the blob is malformed."""
    try:
        payload = json.loads(text)
    except Exception:
        return None
    order = payload.get("order")
    if not isinstance(order, list):
        return None
    try:
        return [int(x) for x in order]
    except (ValueError, TypeError):
        return None
