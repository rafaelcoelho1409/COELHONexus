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
        try:
            import json_repair  # type: ignore

            return json_repair.loads(m.group(0))  # type: ignore
        except Exception:
            return None


def fallback_assign_scores(
    doc_summary: str, doc_terms: list[str], proposals: list[dict],
    source_key: str = "",
) -> list[dict]:
    """Lexical fallback when the assign LLM fails — routes doc to best word-overlap chapter so it isn't silently dropped.

    Ties (including the common all-zero-overlap case — thin distillate,
    short chapter descriptions) are broken by a stable hash of source_key,
    not chapter index. Argmax-style first-index tie-breaking silently piled
    every zero-signal doc into whichever chapter happened to be proposed
    first, turning it into an incoherent grab-bag under any sustained LLM
    outage (chapter_assign 47.5% fallback rate on a small corpus put 25/40
    docs in chapter 0 alone — see 2026-09-04 browser-use Planner run)."""
    if not proposals:
        return []
    dw = {
        w for w in FB_WORD_RE.findall(
            (doc_summary + " " + " ".join(doc_terms)).lower()
        )
        if w not in FB_STOP
    }
    overlaps = []
    for p in proposals:
        text = (
            (p.get("title") or "") + " " + (p.get("description") or "")
            + " " + " ".join(p.get("key_concepts") or [])
        )
        pw = {
            w for w in FB_WORD_RE.findall(text.lower())
            if w not in FB_STOP
        }
        overlaps.append(len(dw & pw))
    best_ov = max(overlaps)
    tied = [i for i, ov in enumerate(overlaps) if ov == best_ov]
    if len(tied) == 1:
        best_i = tied[0]
    else:
        h = int(sha256((source_key or doc_summary).encode()).hexdigest(), 16)
        best_i = tied[h % len(tied)]
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
