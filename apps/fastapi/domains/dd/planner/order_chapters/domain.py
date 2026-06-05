"""order_chapters — pure helpers (parse, Borda aggregate, foundational
prefix rule, outline loader). Prompt builder lives in prompts.py."""
from __future__ import annotations

import json

from .patterns import FOUNDATIONAL_RE, JSON_RE


def load_outline(text: str) -> dict:
    """Parse the chapter-select outline. {} on empty/malformed input."""
    try:
        return json.loads(text or "") or {}
    except Exception:
        return {}


def is_foundational(title: str) -> bool:
    """install/setup/cli/quickstart — must anchor at position 0."""
    if not title:
        return False
    return bool(FOUNDATIONAL_RE.search(title))


def parse_order_response(text: str, n_chapters: int) -> list[int] | None:
    """Permutation of [0, n) or None. Strict: any duplicate or out-of-range → None."""
    if not text:
        return None
    try:
        parsed = json.loads(text.strip())
    except Exception:
        m = JSON_RE.search(text)
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


def borda_aggregate(
    rankings: list[list[int]], n_chapters: int,
) -> list[int]:
    """Borda count across rank permutations. Score = sum(n - pos - 1) per
    chapter. Ties broken stably by earliest-ranking appearance (preserves
    bandit primary-deployment opinion). Empty → identity permutation."""
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
    return sorted(
        range(n_chapters),
        key = lambda i: (-scores[i], first_appearance[i]),
    )


def apply_foundational_prefix_rule(
    order: list[int],
    chapters: list[dict],
) -> tuple[list[int], list[int]]:
    """Pin foundational chapters to the FRONT, preserving their relative
    Borda order. Returns (new_order, promoted_indices) for telemetry."""
    foundational_indices = [
        i for i, ch in enumerate(chapters)
        if is_foundational(ch.get("title", ""))
    ]
    if not foundational_indices:
        return list(order), []
    foundational_set = set(foundational_indices)
    fnd_in_order = [i for i in order if i in foundational_set]
    rest = [i for i in order if i not in foundational_set]
    return fnd_in_order + rest, fnd_in_order


def load_chapter_order(text: str) -> list[int] | None:
    """Convenience loader for plan_write. None on malformed blob."""
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
