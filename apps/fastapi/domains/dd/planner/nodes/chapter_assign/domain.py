"""chapter_assign — pure helpers (JSON parse, lexical fallback assigner,
manifest hash). Prompt builder lives in prompts.py; Pydantic schemas in
schemas.py."""
from __future__ import annotations
from . import params, patterns, versions

import json
from hashlib import sha256
from typing import Optional

import json_repair  # type: ignore



def parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = patterns.JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
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
        w for w in patterns.FB_WORD_RE.findall(
            (doc_summary + " " + " ".join(doc_terms)).lower()
        )
        if w not in params.FB_STOP
    }
    overlaps = []
    for p in proposals:
        text = (
            (p.get("title") or "") + " " + (p.get("description") or "")
            + " " + " ".join(p.get("key_concepts") or [])
        )
        pw = {
            w for w in patterns.FB_WORD_RE.findall(text.lower())
            if w not in params.FB_STOP
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
        "confidence":  params.CONFIDENCE_THRESHOLD,
    }]


def apply_rescue_pass(
    assignments: dict[str, list[dict]],
) -> list[dict]:
    """Docs in [RESCUE_FLOOR, CONFIDENCE_THRESHOLD) get their best score
    floored to threshold so they aren't silently dropped at chapter_select.
    Mutates each doc's `scores` list in place; returns the rescued-doc log."""
    rescued: list[dict] = []
    for k, scores in assignments.items():
        if not scores:
            continue
        best_idx = 0
        best_conf = float(scores[0].get("confidence") or 0.0)
        for i, s in enumerate(scores[1:], 1):
            c = float(s.get("confidence") or 0.0)
            if c > best_conf:
                best_conf = c
                best_idx = i
        if best_conf < params.CONFIDENCE_THRESHOLD and best_conf >= params.RESCUE_FLOOR:
            original = best_conf
            scores[best_idx]["confidence"] = params.CONFIDENCE_THRESHOLD
            scores[best_idx]["rescued_from"] = original
            rescued.append({
                "key":           k,
                "chapter_idx":   scores[best_idx]["chapter_idx"],
                "original_conf": original,
            })
    return rescued


def compute_coverage_count(
    assignments: dict[str, list[dict]], n_proposals: int,
) -> dict[int, int]:
    """Per-chapter count of docs assigned at/above CONFIDENCE_THRESHOLD."""
    coverage_count: dict[int, int] = {i: 0 for i in range(n_proposals)}
    for scores in assignments.values():
        for s in scores:
            if s["confidence"] >= params.CONFIDENCE_THRESHOLD:
                coverage_count[s["chapter_idx"]] = coverage_count.get(
                    s["chapter_idx"], 0,
                ) + 1
    return coverage_count


def manifest_hash(
    *,
    slug: str,
    proposals_ref: str,
    source_keys: list[str],
) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|")
    h.update(proposals_ref.encode())
    for k in sorted(source_keys):
        h.update(b"|")
        h.update(k.encode())
    return h.hexdigest()[:16]
