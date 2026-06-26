"""sawc_derive service — pure-function helpers (no I/O, no LLM calls).

Pieces:
  - `is_thin_block(body)`        — signature/length heuristic
  - `build_analogical_prompt`    — Yasunaga 2023 (arXiv:2310.01714)
  - `parse_code_block`           — strip fences, language-aware
  - `score_derived_candidate`    — AST-validity + LOC + import richness
  - `rank_mpsc_samples`          — N candidates → winning index (or None)
  - `python_ast_valid(body)`     — boolean compile gate

All decisions are deterministic given the inputs. The node module
threads I/O (bandit rotator, MinIO persistence, Redis cancel flags).
"""
from __future__ import annotations

import ast
import re

import asyncio
import json
import logging
import os
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState
from ..vault.domain import sentinelize_doc
from ..vault.schemas import VaultEntry

from .keys import (
    derive_latest_key,
    ingestion_source_key,
    sawc_latest_key,
)
from .params import (
    CONCURRENCY,
    DD_PROCESS,
    DD_PROCESS_REEXPLAIN,
    ENV_ENABLED,
    MAX_DERIVES_PER_CHAPTER,
    MAX_OUTPUT_TOKENS,
    N_MPSC_SAMPLES,
    REEXPLAIN_MAX_TOKENS,
)
from .schemas import DeriveAttempt, DeriveStats


logger = logging.getLogger(__name__)


def _env_enabled() -> bool:
    """Default ON; explicit 'false' / '0' / 'no' / 'off' disables.

    Restored 2026-06-07 — lost in the per-node refactor split (was a
    module-local helper in the old monolithic `sawc_derive/node.py`).
    Pairs with the `ENV_ENABLED` env-var name constant in `params.py`."""
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return True
    return False

from .params import (
    DERIVED_MAX_CHARS,
    DERIVED_MAX_LINES,
    DERIVED_MIN_CHARS,
    DERIVED_MIN_LINES,
    THIN_MAX_CHARS,
    THIN_MAX_NEWLINES,
)
from .patterns import SIGNATURE_ONLY_RE


# Thin-block detection
def is_thin_block(body: str) -> bool:
    """True when a vault code body is too thin to teach effectively.

    "Thin" = signature-only OR very short. The two-gate AND lets a
    short example like a 4-line snippet through, while catching:
        list_skills(client: Client) -> list[SkillSummary]
    and similar single-line API references.

    The heuristic is conservative on purpose — we'd rather miss a
    derive opportunity than over-fire and re-generate already-good
    code blocks.
    """
    if not body:
        return False
    stripped = body.strip()
    if not stripped:
        return False
    n_newlines = stripped.count("\n")
    if len(stripped) > THIN_MAX_CHARS:
        return False
    if n_newlines > THIN_MAX_NEWLINES:
        return False
    # Single non-empty line that looks like a signature → thin.
    if n_newlines == 0 and SIGNATURE_ONLY_RE.match(stripped):
        return True
    # 1-2 newlines but content fits the signature shape line-wise — also thin.
    if n_newlines <= THIN_MAX_NEWLINES:
        non_empty_lines = [
            ln for ln in stripped.splitlines() if ln.strip()
        ]
        if len(non_empty_lines) <= 2 and all(
            SIGNATURE_ONLY_RE.match(ln.strip()) for ln in non_empty_lines
        ):
            return True
    # Otherwise, fall through — short but isn't a pure signature.
    return False


# Prompt builder — Analogical Prompting (Yasunaga 2023 arXiv:2310.01714)
def build_reexplain_prompt(
    *,
    framework: str,
    section_heading: str,
    subheading: str,
    old_explanation: str,
    derived_code: str,
    lang: str = "python",
) -> str:
    """Ship D (2026-05-25): after MPSC promotes a derived code block,
    the original explanation (written for the thin signature) is stale —
    it describes APIs/params from the signature, not the expanded
    example. This prompt regenerates the explanation conditioned on
    the new code body.

    Per the deep research (Citation-Grounded Code Comprehension arXiv
    2512.12117): prose grounded to the resolved code beats prose grounded
    to an imagined topic. The re-explain call mirrors the Ship A "hash
    first, prose second" ordering — the code is already chosen; we just
    rewrite the prose to match.
    """
    return (
        f"You are regenerating ONE documentation explanation in a "
        f"{framework} learning resource. The code block below has been "
        f"newly AI-generated to expand a thin signature; the old "
        f"explanation no longer describes it. Write a fresh explanation "
        f"that grounds to THIS specific code.\n\n"
        f"SECTION: {section_heading}\n"
        f"SUBTOPIC: {subheading}\n\n"
        f"OLD EXPLANATION (stale — describes a different example):\n"
        f"{old_explanation.strip()}\n\n"
        f"NEW CODE BLOCK:\n"
        f"```{lang}\n{derived_code.strip()}\n```\n\n"
        f"== TASK ==\n"
        f"Write a NEW explanation (8-80 words, 1-3 sentences) that:\n"
        f"  1. Describes WHAT this specific code block demonstrates.\n"
        f"  2. References at least ONE identifier visible in the code "
        f"(function name, decorator, type, parameter, or imported "
        f"symbol).\n"
        f"  3. Reads as prose that goes IMMEDIATELY BEFORE the code in a "
        f"cookbook chapter.\n"
        f"  4. NO code fences, NO inline `code-ref` tags, NO meta-framing "
        f"('In this example...'). Just the explanation.\n\n"
        f"OUTPUT: strict JSON, exactly: "
        f'{{"explanation": "your rewritten 8-80 word explanation here"}}\n'
        f"NO prose commentary outside JSON."
    )


def build_analogical_prompt(
    *,
    framework: str,
    chapter_title: str,
    section_heading: str,
    subheading: str,
    explanation: str,
    original_body: str,
    original_lang: str = "python",
) -> str:
    """Analogical Prompting prompt — ask the LLM to first reason about
    a relevant, expanded example by analogy, then emit it as a fenced
    code block.

    Per Yasunaga et al. 2023 ("Large Language Models as Analogical
    Reasoners"), letting the model first describe a closely-related
    canonical example improves derived-code quality vs. one-shot
    generation. We don't need the reasoning text in the output —
    we strip everything outside the final fenced block server-side.

    Output contract: exactly one fenced code block in the response.
    Anything outside the fence is discarded.
    """
    return (
        f"You are expanding a thin documentation reference into a "
        f"COMPLETE RUNNABLE EXAMPLE for a {framework} learning resource.\n\n"
        f"CHAPTER: {chapter_title}\n"
        f"SECTION: {section_heading}\n"
        f"SUBTOPIC: {subheading}\n"
        f"PROSE LEAD-IN (already written, do NOT repeat): "
        f"{explanation}\n\n"
        f"== ORIGINAL DOC REFERENCE (too thin to teach) ==\n"
        f"```{original_lang}\n"
        f"{original_body.strip()}\n"
        f"```\n\n"
        f"== TASK ==\n"
        f"Think about ONE common production use-case that exercises this "
        f"API. By analogy to that use-case, write a self-contained, "
        f"runnable {original_lang} example demonstrating realistic usage. "
        f"Show real imports, real arguments, real return-value handling.\n\n"
        f"== HARD RULES ==\n"
        f"1. Output EXACTLY ONE fenced ```{original_lang} ... ``` block. "
        f"NO prose before, after, or between fences.\n"
        f"2. The code MUST parse as valid {original_lang} (AST validates "
        f"it server-side; ungated samples are discarded).\n"
        f"3. Length: 4-50 non-blank lines. Tight, focused, teachable.\n"
        f"4. INCLUDE imports for any types/decorators used.\n"
        f"5. Use REAL function/method names from {framework} — do NOT "
        f"invent APIs. If unsure, mirror the surface from the original "
        f"reference above; expand parameter names + types realistically.\n"
        f"6. NO placeholders like '...', 'YOUR_KEY_HERE', '# TODO'. "
        f"Concrete, usable values everywhere.\n"
        f"7. NO test scaffolding (no `assert`, no `unittest`, no "
        f"`pytest.mark`). Production-style code only.\n"
        f"8. NO inline comments explaining what the code does line-by-"
        f"line — the prose lead-in already framed it.\n\n"
        f"Respond with the fenced code block ONLY."
    )


# Code-block extraction
_FENCE_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)\n```",
    re.DOTALL,
)


def parse_code_block(raw: str) -> str:
    """Extract the first fenced code block from an LLM response.

    Returns the inner body (no fences). Empty string if no fenced
    block is present — the caller treats that as a failed sample.
    """
    if not raw:
        return ""
    m = _FENCE_RE.search(raw)
    if not m:
        # Last-resort fallback: if the whole response is plausibly
        # bare code (no fences at all), return it. AST parse downstream
        # is the real gate.
        stripped = raw.strip()
        if "```" not in stripped and stripped:
            return stripped
        return ""
    return m.group(1).rstrip("\n")


# AST validity gate (Python-only for now; sawc_write subtopics
# overwhelmingly target python — extend per-lang if framework demands)
def python_ast_valid(body: str) -> bool:
    """True iff `body` parses as valid Python (incl. async). Catches
    hallucinated names, malformed signatures, broken imports."""
    if not body or not body.strip():
        return False
    try:
        ast.parse(body)
        return True
    except SyntaxError:
        return False
    except Exception:
        # Any other parser-internal failure → treat as invalid.
        return False


# Per-sample scoring
def score_derived_candidate(body: str) -> float:
    """Deterministic structural score for one derived candidate.

    Higher = better. Used by `rank_mpsc_samples` to break ties among
    AST-valid samples.

    Components (~0-10 scale):
      + AST valid:        4.0
      + In LOC band:      2.0
      + Has imports:      1.5  (real lib usage signal)
      + Multi-line:       1.0  (not a one-shot expression)
      − Excess length:    up to -2.0 (penalize blobs >40 lines)
      − Placeholder leak: -3.0 (`...`, `YOUR_*_HERE`, `# TODO`, etc.)
    """
    if not body or not body.strip():
        return -10.0
    score = 0.0
    if python_ast_valid(body):
        score += 4.0
    lines = [ln for ln in body.splitlines() if ln.strip()]
    n_lines = len(lines)
    if DERIVED_MIN_LINES <= n_lines <= DERIVED_MAX_LINES:
        score += 2.0
    n_imports = sum(
        1 for ln in lines
        if re.match(r"^\s*(?:from\s+\w+|import\s+\w+)", ln)
    )
    if n_imports >= 1:
        score += 1.5
    if n_lines >= 3:
        score += 1.0
    if n_lines > 40:
        score -= min(2.0, (n_lines - 40) * 0.1)
    # Placeholder leaks — clear hallmarks of unfinished code.
    placeholders = (
        "YOUR_KEY_HERE", "YOUR_API_KEY", "# TODO", "# FIXME",
        "pass  # implement", "raise NotImplementedError",
    )
    body_lower = body
    for p in placeholders:
        if p in body_lower:
            score -= 3.0
            break
    if re.search(r"^\s*\.{3}\s*$", body, re.MULTILINE):
        score -= 3.0
    return round(score, 3)


# MPSC ranker — Multi-Path Self-Consistency (arXiv 2503.04611)
def rank_mpsc_samples(samples: list[str]) -> tuple[int | None, list[float]]:
    """Pick the best AST-valid sample by structural score.

    Returns (chosen_idx, scores). chosen_idx is None when no sample is
    both AST-valid AND in the LOC band — caller then records the attempt
    as `rejected_ast` (no AST pass) or `rejected_len` (AST passed but
    nothing in band).
    """
    if not samples:
        return None, []
    scores = [score_derived_candidate(s) for s in samples]
    # Require AST validity + length-band; pick highest score among those.
    valid_idxs = [
        i for i, s in enumerate(samples)
        if python_ast_valid(s)
    ]
    if not valid_idxs:
        return None, scores
    in_band: list[int] = []
    for i in valid_idxs:
        body = samples[i]
        n_lines = sum(1 for ln in body.splitlines() if ln.strip())
        n_chars = len(body)
        if (DERIVED_MIN_LINES <= n_lines <= DERIVED_MAX_LINES
                and DERIVED_MIN_CHARS <= n_chars <= DERIVED_MAX_CHARS):
            in_band.append(i)
    if not in_band:
        return None, scores
    chosen = max(in_band, key=lambda i: scores[i])
    return chosen, scores


async def _load_referenced_vault_entries(
    minio,
    slug: str,
    needed_hashes: set[str],
    source_keys: list[str],
) -> dict[str, VaultEntry]:
    """Walk the chapter's per_source list, sentinelize each raw doc, and
    return only the entries whose hash is in `needed_hashes`. Mirrors
    sawc_write's runtime fallback but trims to just what derive needs.
    """
    found: dict[str, VaultEntry] = {}
    if not needed_hashes:
        return found
    for source_key in source_keys:
        if not source_key or not source_key.startswith("ingestion/"):
            continue
        try:
            raw = await minio.read_text(source_key)
            if not raw or "<code-ref hash=" in raw:
                continue
            _, entries = sentinelize_doc(raw)
            for h, entry in (entries or {}).items():
                if h in needed_hashes and h not in found:
                    found[h] = entry
            if len(found) == len(needed_hashes):
                break  # short-circuit when all hashes are covered
        except Exception as e:
            logger.debug(
                f"[sawc_derive] vault read for {source_key!r} failed: "
                f"{type(e).__name__}: {e}"
            )
            continue
    return found


async def _reexplain_one(
    *,
    framework: str,
    section_heading: str,
    subheading: str,
    old_explanation: str,
    derived_code: str,
) -> Optional[str]:
    """Fire ONE bandit-routed call to regenerate the explanation
    against the newly-promoted derived_code. Returns the new explanation
    string or None on any failure (caller keeps old explanation).
    """
    import json as _json

    prompt = build_reexplain_prompt(
        framework=framework,
        section_heading=section_heading,
        subheading=subheading,
        old_explanation=old_explanation,
        derived_code=derived_code,
        lang="python",
    )
    try:
        response, _meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=REEXPLAIN_MAX_TOKENS,
            temperature=0.4,
            dd_process=DD_PROCESS_REEXPLAIN,
        )
    except Exception as e:
        logger.debug(
            f"[sawc_derive] re-explain call failed: {type(e).__name__}: {e}"
        )
        return None
    if not response:
        return None
    import re as _re
    m = _re.search(r"\{.*\}", response, _re.DOTALL)
    if not m:
        return None
    try:
        parsed = _json.loads(m.group())
    except Exception:
        return None
    expl = (parsed.get("explanation") or "").strip()
    if not expl:
        return None
    # Tight sanity: 6-100 words, no code fences. Pydantic's 8-80 floor
    # will reject the subtopic if we miss too far, so be permissive here.
    words = expl.split()
    if len(words) < 6 or len(words) > 100:
        return None
    if "```" in expl or "<code-ref" in expl or "<code id" in expl:
        return None
    return expl


async def _sample_one(prompt: str) -> tuple[str, Optional[str], int]:
    """Single bandit-routed LLM call. Returns (body_text, deployment, wall_ms).
    Body is empty on failure; caller decides how to count it."""
    t0 = time.monotonic()
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.7,
            dd_process=DD_PROCESS,
        )
        deployment = (meta or {}).get("deployment")
        body = parse_code_block(response or "")
        return body, deployment, int((time.monotonic() - t0) * 1000)
    except Exception as e:
        logger.debug(
            f"[sawc_derive] sample failed: {type(e).__name__}: {e}"
        )
        return "", None, int((time.monotonic() - t0) * 1000)


async def _derive_one_subtopic(
    *,
    section_id: str,
    subtopic: dict,
    original_body: str,
    framework: str,
    chapter_title: str,
    section_heading: str,
    sem: asyncio.Semaphore,
) -> DeriveAttempt:
    """Run MPSC for one subtopic. Mutates `subtopic` IN PLACE on success.
    Returns the attempt record either way."""
    async with sem:
        original_chars = len(original_body or "")
        original_lines = (original_body or "").count("\n") + 1
        sub_meta = dict(
            section_id=section_id,
            subheading=str(subtopic.get("subheading") or ""),
            code_ref_hash=str(subtopic.get("code_ref_hash") or ""),
            original_chars=original_chars,
            original_lines=original_lines,
        )
        t0 = time.monotonic()
        prompt = build_analogical_prompt(
            framework=framework,
            chapter_title=chapter_title,
            section_heading=section_heading,
            subheading=str(subtopic.get("subheading") or ""),
            explanation=str(subtopic.get("explanation") or ""),
            original_body=original_body,
        )
        results = await asyncio.gather(
            *[_sample_one(prompt) for _ in range(N_MPSC_SAMPLES)],
            return_exceptions=False,
        )
        bodies = [r[0] for r in results]
        deployment = next((r[1] for r in results if r[1]), None)
        n_valid = sum(1 for b in bodies if b and python_ast_valid(b))
        chosen_idx, _scores = rank_mpsc_samples(bodies)
        wall_ms = int((time.monotonic() - t0) * 1000)
        if chosen_idx is None:
            decision = "rejected_ast" if n_valid == 0 else "rejected_len"
            if not any(bodies):
                decision = "rotator_fail"
            return DeriveAttempt(
                decision=decision,
                n_samples_tried=N_MPSC_SAMPLES,
                n_samples_valid=n_valid,
                deployment=deployment,
                wall_ms=wall_ms,
                **sub_meta,
            )
        winner = bodies[chosen_idx]
        # ── Mutate subtopic in place ────────────────────────────────────
        subtopic["code_source"] = "derived"
        subtopic["derived_code"] = winner
        # The original explanation was written for the thin signature;
        # the derived code is a different (richer) example. Regenerate
        # the explanation conditioned on the new code body so prose↔code
        # alignment holds. One extra bandit call per promotion.
        new_expl = await _reexplain_one(
            framework=framework,
            section_heading=section_heading,
            subheading=str(subtopic.get("subheading") or ""),
            old_explanation=str(subtopic.get("explanation") or ""),
            derived_code=winner,
        )
        if new_expl:
            subtopic["explanation"] = new_expl

        return DeriveAttempt(
            decision="promoted",
            derived_chars=len(winner),
            derived_lines=sum(1 for ln in winner.splitlines() if ln.strip()),
            n_samples_tried=N_MPSC_SAMPLES,
            n_samples_valid=n_valid,
            chosen_sample_idx=chosen_idx,
            deployment=deployment,
            wall_ms=wall_ms,
            **sub_meta,
        )


async def sawc_derive_run(state: SynthState) -> dict:
    """Enrich thin subtopics with AI-derived runnable examples."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "derive_stats": {
                "skipped":  "no_slug_or_chapter_id",
                "wall_ms":  0,
            },
        }

    t0 = time.monotonic()
    minio = get_storage()
    sawc_key = sawc_latest_key(slug, chapter_id)

    if not await minio.exists(sawc_key):
        # sawc_write failed or hasn't run — nothing to do.
        await emit_progress(
            thread_id, "sawc_derive", "skipped",
            chapter_id=chapter_id, reason="sawc_latest_missing",
        )
        return {
            "derive_stats": {
                "skipped": "sawc_latest_missing",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
        }

    try:
        sawc_payload = json.loads(await minio.read_text(sawc_key))
    except Exception as e:
        logger.warning(
            f"[sawc_derive] sawc-latest unreadable: {type(e).__name__}: {e}"
        )
        await emit_progress(
            thread_id, "sawc_derive", "skipped",
            chapter_id=chapter_id, reason="sawc_unreadable",
        )
        return {
            "derive_stats": {
                "skipped": "sawc_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
        }

    sections: list[dict] = sawc_payload.get("sections") or []
    chapter_title = (
        sawc_payload.get("chapter_title")
        or sawc_payload.get("chapter_id")
        or chapter_id
    )
    framework = sawc_payload.get("framework_slug") or slug

    # Total subtopics + signature-only candidates.
    n_subtopics_total = 0
    for s in sections:
        n_subtopics_total += len(s.get("subtopics") or [])

    enabled = _env_enabled()
    await emit_progress(
        thread_id, "sawc_derive", "start",
        chapter_id=chapter_id, enabled=enabled,
        n_subtopics_total=n_subtopics_total,
    )

    if not enabled:
        attempts = [
            DeriveAttempt(
                section_id=str(s.get("section_id") or ""),
                subheading=str(st.get("subheading") or ""),
                code_ref_hash=str(st.get("code_ref_hash") or ""),
                original_chars=0,
                original_lines=0,
                decision="disabled",
            )
            for s in sections for st in (s.get("subtopics") or [])
        ]
        stats = DeriveStats(
            chapter_id=chapter_id,
            framework_slug=framework,
            enabled=False,
            n_subtopics_total=n_subtopics_total,
            n_candidates_thin=0,
            n_promoted=0,
            n_rejected_ast=0,
            n_rejected_len=0,
            n_rotator_fail=0,
            wall_ms=int((time.monotonic() - t0) * 1000),
            attempts=attempts,
        )
        await minio.write(
            derive_latest_key(slug, chapter_id),
            json.dumps(stats.model_dump(), indent=2),
            content_type="application/json",
        )
        await emit_progress(
            thread_id, "sawc_derive", "done",
            chapter_id=chapter_id, **{
                k: v for k, v in stats.model_dump(exclude={"attempts"}).items()
            },
        )
        return {"derive_stats": stats.model_dump()}

    # ── Locate thin candidates ─────────────────────────────────────────
    needed_hashes: set[str] = set()
    candidates: list[tuple[dict, dict, str]] = []  # (section, subtopic, expected_hash)
    for s in sections:
        sub_list = s.get("subtopics") or []
        for st in sub_list:
            if not isinstance(st, dict):
                continue
            if st.get("code_source") == "derived":
                continue  # already derived
            h = st.get("code_ref_hash") or ""
            if h:
                needed_hashes.add(h)
                candidates.append((s, st, h))

    # Pull vault entries for those hashes.
    per_source = sawc_payload.get("per_source") or []
    if not per_source:
        # sawc blob doesn't carry per_source; reconstruct from citations.
        cite_sources: set[str] = set()
        for s in sections:
            for c in (s.get("citations") or []):
                k = c.get("source_key") or ""
                if k:
                    cite_sources.add(k)
        source_keys = sorted(cite_sources)
    else:
        source_keys = sorted({x.get("source_key") for x in per_source if x})

    vault = await _load_referenced_vault_entries(
        minio, slug, needed_hashes, source_keys,
    )

    thin_candidates: list[tuple[dict, dict, str, str]] = []  # +original_body
    for sec, st, h in candidates:
        entry = vault.get(h)
        body = ""
        if entry is not None:
            body = entry.fence_text or ""
        if body and is_thin_block(body):
            thin_candidates.append((sec, st, h, body))
    # Cap by burst protection.
    if len(thin_candidates) > MAX_DERIVES_PER_CHAPTER:
        thin_candidates = thin_candidates[:MAX_DERIVES_PER_CHAPTER]

    n_candidates_thin = len(thin_candidates)
    await emit_progress(
        thread_id, "sawc_derive", "candidates_identified",
        n_candidates_thin=n_candidates_thin,
        n_subtopics_total=n_subtopics_total,
        vault_entries_loaded=len(vault),
    )

    # ── Fan out MPSC sampling ─────────────────────────────────────────
    sem = asyncio.Semaphore(CONCURRENCY)
    derive_tasks = [
        _derive_one_subtopic(
            section_id=str(sec.get("section_id") or ""),
            subtopic=st,
            original_body=body,
            framework=framework,
            chapter_title=chapter_title,
            section_heading=str(sec.get("heading") or ""),
            sem=sem,
        )
        for (sec, st, h, body) in thin_candidates
    ]
    if derive_tasks:
        results: list[DeriveAttempt] = await asyncio.gather(*derive_tasks)
    else:
        results = []

    # Account for non-thin subtopics so the attempts log is complete.
    thin_set = {id(st) for _, st, _, _ in thin_candidates}
    skipped_attempts: list[DeriveAttempt] = []
    for sec, st, h in candidates:
        if id(st) in thin_set:
            continue
        body = ""
        entry = vault.get(h)
        if entry is not None:
            body = entry.fence_text or ""
        skipped_attempts.append(DeriveAttempt(
            section_id=str(sec.get("section_id") or ""),
            subheading=str(st.get("subheading") or ""),
            code_ref_hash=h,
            original_chars=len(body),
            original_lines=(body.count("\n") + 1) if body else 0,
            decision="skipped_thin",
        ))

    all_attempts = list(results) + skipped_attempts

    n_promoted = sum(1 for a in results if a.decision == "promoted")
    n_rejected_ast = sum(1 for a in results if a.decision == "rejected_ast")
    n_rejected_len = sum(1 for a in results if a.decision == "rejected_len")
    n_rotator_fail = sum(1 for a in results if a.decision == "rotator_fail")

    # ── Mutate sawc-latest.json in place if any derives promoted ─────
    if n_promoted > 0:
        try:
            await minio.write(
                sawc_key,
                json.dumps(sawc_payload, indent=2, ensure_ascii=False),
                content_type="application/json",
            )
        except Exception as e:
            logger.error(
                f"[sawc_derive] failed to persist mutated sawc-latest: "
                f"{type(e).__name__}: {e}"
            )

    stats = DeriveStats(
        chapter_id=chapter_id,
        framework_slug=framework,
        enabled=True,
        n_subtopics_total=n_subtopics_total,
        n_candidates_thin=n_candidates_thin,
        n_promoted=n_promoted,
        n_rejected_ast=n_rejected_ast,
        n_rejected_len=n_rejected_len,
        n_rotator_fail=n_rotator_fail,
        wall_ms=int((time.monotonic() - t0) * 1000),
        attempts=all_attempts,
    )

    try:
        await minio.write(
            derive_latest_key(slug, chapter_id),
            json.dumps(stats.model_dump(), indent=2),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(
            f"[sawc_derive] failed to persist derive-latest: "
            f"{type(e).__name__}: {e}"
        )

    await emit_progress(
        thread_id, "sawc_derive", "done",
        chapter_id=chapter_id,
        n_subtopics_total=n_subtopics_total,
        n_candidates_thin=n_candidates_thin,
        n_promoted=n_promoted,
        n_rejected_ast=n_rejected_ast,
        n_rejected_len=n_rejected_len,
        n_rotator_fail=n_rotator_fail,
        wall_ms=stats.wall_ms,
    )

    logger.info(
        f"[sawc_derive] {slug}/{chapter_id}: "
        f"{n_promoted}/{n_candidates_thin} promoted "
        f"(ast_fail={n_rejected_ast}, len_fail={n_rejected_len}, "
        f"rotator_fail={n_rotator_fail}); {stats.wall_ms} ms"
    )

    return {"derive_stats": stats.model_dump()}
