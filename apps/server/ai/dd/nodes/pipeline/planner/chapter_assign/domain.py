"""chapter_assign — pure helpers (the scorer's request, lexical fallback assigner, rescue pass, manifest hash). The scorer's contract
lives in `agents.pipeline.planner.schemas`; its prompt in `prompts.pipeline.planner.page_scorer`."""
from __future__ import annotations
from . import params, patterns, versions

import json
import re
from hashlib import sha256


def score_payload(
    *, framework: str, source_key: str, doc_path: str, summary: str, terms: list[str], body: str, note: str = "",
) -> str:
    """The scorer's request for one page: its distillate (or, without one, a body excerpt) and its URL path; `note` is a
    follow-up instruction for a re-ask (the chapters a partial answer skipped, or the closest-chapter line for an all-zero one)."""
    request: dict = {"task": "Score this one file against every chapter.", "framework": framework, "source_key": source_key}
    if doc_path:
        request["page_url_path"] = doc_path
    if summary:
        request["summary"] = summary
        request["key_terms"] = terms[:8]
    else:
        request["body"] = (body or "")[:params.BODY_CHARS]
    if note:
        request["note"] = note
    return json.dumps(request, ensure_ascii = False)


def missing_note(n_scored: int, n_proposals: int, missing: list[int]) -> str:
    return (
        f"Your previous answer scored only {n_scored} of the {n_proposals} chapters. Now return ONE entry for EACH of these "
        f"chapter_idx values and no others: {missing}, with confidence 0.0 where the doc does not apply."
    )


def missing_chapter_idxs(scores: list[dict], n_proposals: int) -> list[int]:
    """Chapter indices a doc's score vector skipped. The prompt asks for ONE entry per proposal, but models sometimes list only a subset (elasticsearch-python: 12 of 694 docs scored 16 of 30 chapters); the skipped chapters then read as confidence 0.0 downstream, i.e. 'not this chapter' rather than 'not judged'."""
    seen = {int(s["chapter_idx"]) for s in scores}
    return [i for i in range(n_proposals) if i not in seen]


def can_skip_reask(scores: list[dict], missing: list[int]) -> bool:
    """A vector missing only a chapter or two whose best score is already confident needs no reask:
    on the 28-chapter langfuse plan 47 of 47 reasks for the single LAST chapter came back 0.0 (15% of
    all assign calls), while the 64 reasks for a cut-off tail (chapters 16-27) found a >= 0.3 score
    76% of the time and stay."""
    if not scores or len(missing) > params.SKIP_REASK_MAX_MISSING:
        return False
    return max(float(s.get("confidence") or 0.0) for s in scores) >= params.SKIP_REASK_MIN_BEST


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
    content_fp: str = "",
) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|")
    h.update(proposals_ref.encode())
    for k in sorted(source_keys):
        h.update(b"|")
        h.update(k.encode())
    if content_fp:
        # proposals_ref is the constant "latest" path; the proposals AND distillates
        # CONTENT is what the scores are computed from.
        h.update(b"|content=")
        h.update(content_fp.encode())
    return h.hexdigest()[:16]


def url_paths(entries: list[dict]) -> dict[str, str]:
    """key -> the page's URL path without host, query, fragment or file extension
    (`https://langfuse.com/integrations/frameworks/agno-agents` -> `integrations/frameworks/agno-agents`).
    `source_path` (the real page URL of a single-URL dump) wins over `url`; the key is absent when the
    manifest has neither or the path is empty."""
    out: dict[str, str] = {}
    for e in entries or []:
        k = e.get("key")
        u = (e.get("source_path") or e.get("url") or "").split("#", 1)[0].split("?", 1)[0]
        path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*/?", "", u.strip()).strip("/")
        path = re.sub(r"\.(html?|md|mdx|php|aspx?)$", "", path)
        if k and path:
            out[k] = path[:params.URL_PATH_MAX_CHARS]
    return out
