"""chapter_assign — pure helpers (JSON parse, lexical fallback assigner,
manifest hash). Prompt builder lives in prompts.py; Pydantic schemas in
schemas.py."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Optional

from .params import CONFIDENCE_THRESHOLD, FB_STOP
from .patterns import FB_WORD_RE, JSON_RE
from .versions import PROMPT_VERSION


def parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def fallback_assign_scores(
    doc_summary: str, doc_terms: list[str], proposals: list[dict],
) -> list[dict]:
    """Lexical-overlap fallback used when the assign LLM call FAILS —
    routes the doc to its best word-overlap chapter at threshold
    confidence so it isn't silently dropped from the book (the
    assign-node equivalent of the doc_distill fallback). Single-
    membership: only the best chapter is scored. Empty when there are no
    proposals to match against."""
    if not proposals:
        return []
    dw = {
        w for w in FB_WORD_RE.findall(
            (doc_summary + " " + " ".join(doc_terms)).lower()
        )
        if w not in FB_STOP
    }
    best_i, best_ov = 0, -1
    for i, p in enumerate(proposals):
        text = (
            (p.get("title") or "") + " " + (p.get("description") or "")
            + " " + " ".join(p.get("key_concepts") or [])
        )
        pw = {
            w for w in FB_WORD_RE.findall(text.lower())
            if w not in FB_STOP
        }
        ov = len(dw & pw)
        if ov > best_ov:
            best_ov, best_i = ov, i
    return [{
        "chapter_idx": best_i,
        "confidence":  CONFIDENCE_THRESHOLD,
    }]


def manifest_hash(
    *,
    slug: str,
    proposals_ref: str,
    source_keys: list[str],
) -> str:
    h = sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|")
    h.update(proposals_ref.encode())
    for k in sorted(source_keys):
        h.update(b"|")
        h.update(k.encode())
    return h.hexdigest()[:16]
