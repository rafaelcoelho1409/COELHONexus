"""book_harmonize — cross-chapter coherence pass (SurveyGen-I Step 11, arXiv 2508.14317)."""
from __future__ import annotations
import domains
from . import domain, params, prompts

import asyncio
import logging
import random
from typing import Optional





logger = logging.getLogger(__name__)

# Call-retry budget before giving up on a single LLM step (extract /
# canonicalize / detect / patch each fail-soft already — a chapter with
# no harmonization pass keeps its original, still-valid prose — but this
# runs once at the END of a potentially multi-hour Study run, so a cheap
# retry against a transient Rotator hiccup is worth it. Same idiom as
# every other synth node.
_MAX_CALL_ATTEMPTS = 2


async def _call_with_retry(
    prompt: str, **kwargs,
) -> tuple[Optional[str], Optional[Exception]]:
    """chat_judge_bandit_async wrapper with a plain retry-with-backoff.
    Returns (raw_text, None) on success or (None, last_error) once
    _MAX_CALL_ATTEMPTS is exhausted — caller logs + applies its own
    fail-soft fallback, unchanged from before this wrapper existed."""
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_CALL_ATTEMPTS):
        try:
            raw, _meta = await domains.llm.rotator.chain.chat_judge_bandit_async(prompt, **kwargs)
            return raw, None
        except Exception as e:
            last_error = e
            if attempt < _MAX_CALL_ATTEMPTS - 1:
                await asyncio.sleep(1.0 + random.random())
    return None, last_error


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

    sem = asyncio.Semaphore(params.PER_CHAPTER_CONCURRENCY)
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
            sibling_claims=domain.pick_sibling_claims(ch["chapter_id"], claims_by_id),
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
                domains.dd.synth.runtime.observability.metrics.record_classical_patch(
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
        chapter_id = chapter.get("chapter_id")
        try:
            prompt = prompts.EXTRACT_CLAIMS_PROMPT.format(
                max_claims=params.MAX_CLAIMS_PER_CHAPTER,
                prose=(chapter.get("prose") or "")[:params.PROSE_CHARS_FOR_CLAIMS],
            )
            raw, err = await _call_with_retry(
                prompt, max_tokens=params.EXTRACT_MAX_TOKENS, temperature=0.0,
                response_format={"type": "json_object"},
                timeout_s=params.EXTRACT_TIMEOUT_S,
            )
            if err is not None:
                raise err
            data = domain.parse_json_block(raw)
            if data is None:
                logger.warning(
                    f"[book_harmonize] claim/term extract for {chapter_id}: "
                    f"no parseable JSON in a {len(raw or '')}-char response "
                    f"(balanced-extract, json.loads, and json_repair all "
                    f"failed) — chapter contributes 0 claims. response "
                    f"prefix: {(raw or '')[:200]!r}"
                )
                return {}
            if "claims" not in data and "terms" not in data:
                # Parsed fine, but not the shape we asked for — silently
                # returning {} here (via .get("claims", [])) is exactly
                # what made ch-04/ch-05's 0-claims outcome undiagnosable
                # on 2026-09-06's 4th study run: valid JSON, wrong keys,
                # no error anywhere. Surface it instead of guessing.
                logger.warning(
                    f"[book_harmonize] claim/term extract for {chapter_id}: "
                    f"JSON parsed but has neither 'claims' nor 'terms' key "
                    f"— got keys {list(data.keys())!r}. response prefix: "
                    f"{(raw or '')[:200]!r}"
                )
                # Issue #21, 2026-09-08: confirmed live (5th full study run)
                # this exact non-compliance shape — the model collapses the
                # requested {"claims": [...], "terms": [...]} envelope down
                # to a single bare term object: {"name": ..., "definition":
                # ...}. Recover it as one term rather than discarding real,
                # usable data — cheap, additive, matches this file's own
                # "recover what you can" precedent (json_repair, balanced-
                # brace retry) rather than inventing a new philosophy.
                if "name" in data:
                    logger.info(
                        f"[book_harmonize] claim/term extract for "
                        f"{chapter_id}: recovered as a single bare term "
                        f"(issue #21)"
                    )
                    return {"claims": [], "terms": [data]}
            return data
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
        prompt = prompts.CANONICALIZE_PROMPT.format(
            framework=framework_name, terms_block=terms_block,
        )
        raw, err = await _call_with_retry(
            prompt, max_tokens=params.CANONICALIZE_MAX_TOKENS, temperature=0.1,
            response_format={"type": "json_object"},
            timeout_s=params.CANONICALIZE_TIMEOUT_S,
        )
        if err is not None:
            raise err
        data = domain.parse_json_block(raw)
        if data is None:
            logger.warning(
                f"[book_harmonize] canonicalize: no parseable JSON in a "
                f"{len(raw or '')}-char response — no canonical terms this "
                f"run. response prefix: {(raw or '')[:200]!r}"
            )
            return []
        return data.get("canonical_terms", []) or []
    except Exception as e:
        logger.warning(
            f"[book_harmonize] canonicalize failed: {type(e).__name__}: {e}"
        )
        return []


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
            prompt = prompts.DETECT_PROMPT.format(
                chapter_id=chapter_id,
                framework=framework_name,
                this_prose=this_prose[:params.PROSE_CHARS_FOR_CLAIMS],
                canonical_terms=domain.format_canonical_terms(canonical_terms),
                sibling_claims=sibling_claims or "(no sibling claims available)",
            )
            raw, err = await _call_with_retry(
                prompt, max_tokens=params.DETECT_MAX_TOKENS, temperature=0.0,
                response_format={"type": "json_object"},
                timeout_s=params.DETECT_TIMEOUT_S,
            )
            if err is not None:
                raise err
            data = domain.parse_json_block(raw)
            if data is None:
                logger.warning(
                    f"[book_harmonize] detect for {chapter_id}: no parseable "
                    f"JSON in a {len(raw or '')}-char response — assuming "
                    f"no violations. response prefix: {(raw or '')[:200]!r}"
                )
                return {"has_violations": False, "violations": [], "summary": ""}
            return data
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
            prompt = prompts.PATCH_PROMPT.format(
                chapter_id=chapter_id,
                framework=framework_name,
                violations_block="\n".join(violations_lines),
                canonical_terms=domain.format_canonical_terms(canonical_terms),
                original_prose=original_prose[:params.PROSE_CHARS_FOR_PATCH],
            )
            raw, err = await _call_with_retry(
                prompt, max_tokens=params.PATCH_MAX_TOKENS, temperature=0.1,
                timeout_s=params.PATCH_TIMEOUT_S,
            )
            if err is not None:
                raise err
            return (raw or "").strip() or None
        except Exception as e:
            logger.warning(
                f"[book_harmonize] patch failed for {chapter_id}: "
                f"{type(e).__name__}: {e}"
            )
            return None
