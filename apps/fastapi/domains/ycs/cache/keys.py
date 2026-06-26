"""ycs/cache — Redis key builder (pure).
Per `docs/CODE-CONVENTIONS.md` §2, key-builder functions belong in
`keys.py` — they're storage path helpers, not loose constants."""
from __future__ import annotations

import hashlib

from .params import CACHE_PREFIX


def cache_key(question: str, mode: str | None = None) -> str:
    """Deterministic 16-char hex suffix on the deprecated prefix.

    Same question + same mode → same key. SHA-256 truncated to 16 hex
    chars (2^64 distinct ids) — collision-free for any plausible cache
    size. Lowercases + strips the question so trivial reformat hits
    the same entry."""
    raw = (question or "").strip().lower()
    if mode:
        raw += f"|mode={mode}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{CACHE_PREFIX}{digest}"
