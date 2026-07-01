"""book_harmonize — cross-chapter coherence pass (SurveyGen-I Step 11, arXiv 2508.14317)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ...runtime.observability import record_classical_patch

from .versions import (
    BOOK_HARMONIZE_PROMPT_VERSION,
    BOOK_HARMONIZE_SCHEMA_VERSION,
)


logger = logging.getLogger(__name__)



_MAX_CLAIMS_PER_CHAPTER = 20
_PROSE_CHARS_FOR_CLAIMS = 10000
_PROSE_CHARS_FOR_PATCH = 16000
_DETECT_MAX_TOKENS = 800
_PATCH_MAX_TOKENS = 14000
_EXTRACT_MAX_TOKENS = 1000
_CANONICALIZE_MAX_TOKENS = 1500
_PER_CHAPTER_CONCURRENCY = 4

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


_EXTRACT_CLAIMS_PROMPT = """Extract the atomic factual claims from this chapter of a distilled technical book.

Atomic claim = a single verifiable assertion about the technology (e.g., "library X
uses Y as its default serializer", "the timeout defaults to 30 seconds"). Cap at
{max_claims}. Skip motivational / structural / transitional sentences.

Also extract the chapter's key terminology — terms the chapter uses for specific
concepts. List them with their working definition AS USED IN THIS CHAPTER.

--- CHAPTER PROSE ---
{prose}
--- END PROSE ---

Return strict JSON:
{{
  "claims": ["claim 1", "claim 2", ...],
  "terms": [{{"name": "term as used", "definition": "1-sentence definition from chapter"}}]
}}"""

_CANONICALIZE_PROMPT = """You are harmonizing terminology across the chapters of a distilled
technical book about {framework}. Below are the terms each chapter uses, with the
working definition the chapter applies.

For each TERM that appears across multiple chapters with DIFFERENT or CONFLICTING
definitions, decide the CANONICAL definition (or merge them if compatible). Skip
terms that are only used in one chapter or that have consistent definitions across
chapters.

--- PER-CHAPTER TERMINOLOGY ---
{terms_block}
--- END ---

Return strict JSON:
{{
  "canonical_terms": [
    {{"term": "name", "canonical_definition": "1-sentence canonical", "affected_chapters": ["ch_id1", "ch_id2"]}}
  ],
  "rationale": "1-sentence explanation of the harmonization choices made"
}}

If no canonicalization is needed, return {{"canonical_terms": [], "rationale": "..."}}."""

_DETECT_PROMPT = """You are auditing chapter {chapter_id} of a distilled technical book about
{framework} for cross-chapter consistency issues.

Inspect for THREE classes of violations:
  1. CONTRADICTION — a claim in this chapter directly contradicts a claim in a sibling chapter
  2. DEFINITION_DRIFT — this chapter uses a term differently than the canonical definition
  3. TERMINOLOGY_DIVERGENCE — this chapter uses one name for a concept that sibling chapters call something else

--- THIS CHAPTER'S PROSE (truncated) ---
{this_prose}
--- END ---

--- CANONICAL TERMINOLOGY BANK ---
{canonical_terms}
--- END ---

--- ATOMIC CLAIMS FROM SIBLING CHAPTERS (sample) ---
{sibling_claims}
--- END ---

Return strict JSON:
{{
  "has_violations": true | false,
  "violations": [
    {{"kind": "contradiction" | "definition_drift" | "terminology_divergence",
      "this_chapter_says": "short quote or paraphrase",
      "should_say": "the canonical or sibling-chapter version",
      "evidence": "short pointer to where in this chapter"}}
  ],
  "summary": "1-sentence overall verdict"
}}

If no violations found, return {{"has_violations": false, "violations": [], "summary": "..."}}."""

_PATCH_PROMPT = """You are minimally rewriting chapter {chapter_id} of a distilled technical book
about {framework} to resolve cross-chapter consistency violations. Preserve EVERYTHING
that isn't violating — same structure, same headings, same code references, same
citations, same tone.

ONLY change the spots flagged below. Use minimal edits — replace conflicting
definitions with canonical ones, swap divergent terms, fix contradictions.

VIOLATIONS TO FIX:
{violations_block}

CANONICAL TERMINOLOGY (use these definitions/names):
{canonical_terms}

--- ORIGINAL CHAPTER (REWRITE THIS, KEEP MARKDOWN STRUCTURE INTACT) ---
{original_prose}
--- END ---

Output: the full chapter prose, minimally edited. NO commentary, NO explanation,
NO JSON wrapping — output ONLY the markdown."""


async def harmonize_book(
    *,
    framework_slug: str,
    framework_name: str,
    chapters: list[dict],
) -> dict:
    """Run the 3-phase cross-chapter harmonization pass; fail-soft per chapter."""
    import time
    t0 = time.monotonic()

    if len(chapters) < 2:
        return {
            "n_chapters": len(chapters),
            "n_atomic_claims": 0,
            "n_canonical_terms": 0,
            "n_chapters_with_issues": 0,
            "n_chapters_patched": 0,
            "patches": [],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "skipped": "less_than_2_chapters",
        }

    sem = asyncio.Semaphore(_PER_CHAPTER_CONCURRENCY)
    extractions = await asyncio.gather(*[
        _extract_claims_and_terms(sem, ch) for ch in chapters
    ])
    claims_by_id: dict[str, list[str]] = {}
    terms_by_id: dict[str, list[dict]] = {}
    for ch, ext in zip(chapters, extractions):
        cid = ch["chapter_id"]
        claims_by_id[cid] = (ext or {}).get("claims", []) or []
        terms_by_id[cid] = (ext or {}).get("terms", []) or []
    total_claims = sum(len(c) for c in claims_by_id.values())
    if total_claims == 0:
        return {
            "n_chapters": len(chapters),
            "n_atomic_claims": 0,
            "n_canonical_terms": 0,
            "n_chapters_with_issues": 0,
            "n_chapters_patched": 0,
            "patches": [],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "skipped": "no_claims_extracted",
        }

    canonical_terms = await _canonicalize_terms(framework_name, terms_by_id)

    detections = await asyncio.gather(*[
        _detect_violations(
            sem=sem,
            chapter_id=ch["chapter_id"],
            framework_name=framework_name,
            this_prose=ch["prose"],
            canonical_terms=canonical_terms,
            sibling_claims=_pick_sibling_claims(ch["chapter_id"], claims_by_id),
        )
        for ch in chapters
    ])

    patches: list[dict] = []
    n_with_issues = 0
    n_patched = 0
    patch_tasks = []
    for ch, det in zip(chapters, detections):
        violations = (det or {}).get("violations", []) or []
        if not violations:
            patches.append({
                "chapter_id": ch["chapter_id"],
                "n_violations": 0,
                "patched": False,
                "new_prose": None,
                "summary": (det or {}).get("summary", ""),
            })
            continue
        n_with_issues += 1
        patch_tasks.append((ch, det, violations))

    if patch_tasks:
        patched_results = await asyncio.gather(*[
            _patch_chapter(
                sem=sem,
                chapter_id=ch["chapter_id"],
                framework_name=framework_name,
                original_prose=ch["prose"],
                violations=violations,
                canonical_terms=canonical_terms,
            )
            for ch, _, violations in patch_tasks
        ])
        for (ch, det, violations), patched_prose in zip(patch_tasks, patched_results):
            ok = bool(patched_prose and len(patched_prose) > 0.5 * len(ch["prose"]))
            patches.append({
                "chapter_id": ch["chapter_id"],
                "n_violations": len(violations),
                "patched": ok,
                "new_prose": patched_prose if ok else None,
                "summary": (det or {}).get("summary", ""),
                "violations": violations,
            })
            if ok:
                n_patched += 1
                record_classical_patch(
                    dim = "cross_chapter_coherence",
                    framework = framework_slug,
                )

    return {
        "n_chapters": len(chapters),
        "n_atomic_claims": total_claims,
        "n_canonical_terms": len(canonical_terms),
        "n_chapters_with_issues": n_with_issues,
        "n_chapters_patched": n_patched,
        "patches": patches,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "skipped": None,
    }


async def _extract_claims_and_terms(
    sem: asyncio.Semaphore, chapter: dict,
) -> dict:
    async with sem:
        try:
            prompt = _EXTRACT_CLAIMS_PROMPT.format(
                max_claims=_MAX_CLAIMS_PER_CHAPTER,
                prose=(chapter.get("prose") or "")[:_PROSE_CHARS_FOR_CLAIMS],
            )
            raw, _ = await chat_judge_bandit_async(
                prompt, max_tokens=_EXTRACT_MAX_TOKENS, temperature=0.0,
                response_format={"type": "json_object"},
            )
            m = _JSON_RE.search(raw or "")
            if not m:
                return {}
            return json.loads(m.group(0))
        except Exception as e:
            logger.warning(
                f"[book_harmonize] claim/term extract failed for "
                f"{chapter.get('chapter_id')}: {type(e).__name__}: {e}"
            )
            return {}


async def _canonicalize_terms(
    framework_name: str, terms_by_id: dict[str, list[dict]],
) -> list[dict]:
    """1 LLM call to resolve term conflicts across chapters."""
    if not any(terms_by_id.values()):
        return []
    lines = []
    for cid, terms in terms_by_id.items():
        if not terms:
            continue
        lines.append(f"## Chapter {cid}")
        for t in terms[:12]:
            name = (t.get("name") or "").strip()
            defn = (t.get("definition") or "").strip()
            if name:
                lines.append(f"  - {name}: {defn[:200]}")
    terms_block = "\n".join(lines)[:8000]
    try:
        prompt = _CANONICALIZE_PROMPT.format(
            framework=framework_name, terms_block=terms_block,
        )
        raw, _ = await chat_judge_bandit_async(
            prompt, max_tokens=_CANONICALIZE_MAX_TOKENS, temperature=0.1,
            response_format={"type": "json_object"},
        )
        m = _JSON_RE.search(raw or "")
        if not m:
            return []
        data = json.loads(m.group(0))
        return data.get("canonical_terms", []) or []
    except Exception as e:
        logger.warning(
            f"[book_harmonize] canonicalize failed: {type(e).__name__}: {e}"
        )
        return []


def _pick_sibling_claims(
    this_id: str, claims_by_id: dict[str, list[str]],
) -> str:
    """Sample sibling-chapter claims into a context-safe blob. Cap at 40
    sibling claims total to keep the detect-prompt within budget."""
    sibling = []
    for cid, cs in claims_by_id.items():
        if cid == this_id:
            continue
        for c in cs[:6]:   # cap per chapter
            sibling.append(f"  [{cid}] {c}")
        if len(sibling) >= 40:
            break
    return "\n".join(sibling[:40])


def _format_canonical_terms(canonical: list[dict]) -> str:
    if not canonical:
        return "(no terminology conflicts detected)"
    lines = []
    for t in canonical[:25]:
        name = (t.get("term") or "").strip()
        defn = (t.get("canonical_definition") or "").strip()
        if name:
            lines.append(f"  - {name}: {defn[:240]}")
    return "\n".join(lines)


async def _detect_violations(
    *,
    sem: asyncio.Semaphore,
    chapter_id: str,
    framework_name: str,
    this_prose: str,
    canonical_terms: list[dict],
    sibling_claims: str,
) -> dict:
    async with sem:
        try:
            prompt = _DETECT_PROMPT.format(
                chapter_id=chapter_id,
                framework=framework_name,
                this_prose=this_prose[:_PROSE_CHARS_FOR_CLAIMS],
                canonical_terms=_format_canonical_terms(canonical_terms),
                sibling_claims=sibling_claims or "(no sibling claims available)",
            )
            raw, _ = await chat_judge_bandit_async(
                prompt, max_tokens=_DETECT_MAX_TOKENS, temperature=0.0,
                response_format={"type": "json_object"},
            )
            m = _JSON_RE.search(raw or "")
            if not m:
                return {"has_violations": False, "violations": [], "summary": ""}
            return json.loads(m.group(0))
        except Exception as e:
            logger.warning(
                f"[book_harmonize] detect failed for {chapter_id}: "
                f"{type(e).__name__}: {e}"
            )
            return {"has_violations": False, "violations": [], "summary": ""}


async def _patch_chapter(
    *,
    sem: asyncio.Semaphore,
    chapter_id: str,
    framework_name: str,
    original_prose: str,
    violations: list[dict],
    canonical_terms: list[dict],
) -> Optional[str]:
    """Run the patch LLM call. Returns None on failure or empty output."""
    async with sem:
        try:
            violations_lines = []
            for v in violations[:12]:
                kind = v.get("kind", "issue")
                says = (v.get("this_chapter_says") or "")[:200]
                should = (v.get("should_say") or "")[:200]
                violations_lines.append(
                    f"  - [{kind}] this chapter: {says!r} → should: {should!r}"
                )
            prompt = _PATCH_PROMPT.format(
                chapter_id=chapter_id,
                framework=framework_name,
                violations_block="\n".join(violations_lines),
                canonical_terms=_format_canonical_terms(canonical_terms),
                original_prose=original_prose[:_PROSE_CHARS_FOR_PATCH],
            )
            raw, _ = await chat_judge_bandit_async(
                prompt, max_tokens=_PATCH_MAX_TOKENS, temperature=0.1,
            )
            return (raw or "").strip() or None
        except Exception as e:
            logger.warning(
                f"[book_harmonize] patch failed for {chapter_id}: "
                f"{type(e).__name__}: {e}"
            )
            return None
