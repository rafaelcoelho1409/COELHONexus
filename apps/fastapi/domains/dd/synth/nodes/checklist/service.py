"""checklist_eval service — deterministic pre-gates, aggregation, rendering,
prompt builders, and LLM verdict coercion."""
from __future__ import annotations
from .keys import (
    digest_latest_key,
    digest_latest_key as _digest_latest_key,
    latest_blob_key,
    latest_blob_key as _latest_blob_key,
    sawc_latest_key,
    sawc_latest_key as _sawc_latest_key,
    versioned_blob_key,
    versioned_blob_key as _versioned_blob_key,
)
from .params import (
    DENSITY_MAX_AVG_EXPLANATION_WORDS,
    DENSITY_MAX_CHARS_PER_PARA,
    DENSITY_MIN_AVG_EXPLANATION_WORDS,
    DENSITY_MIN_CHARS_PER_PARA,
    FEEDBACK_MAX_CHARS,
    FEEDBACK_MIN_CHARS,
    LLM_CRITERIA,
    LLM_CRITERIA as _LLM_CRITERIA,
    MAX_RENDERED_CHAPTER_CHARS,
    MIN_AVG_CODE_REFS_PER_SECTION,
    MIN_CITATIONS_PER_SECTION,
    MIN_CODE_REF_COVERAGE_FRACTION,
    PASS_THRESHOLD,
    PICKER_FALLBACK_RATE_MAX,
    REPAIR_RATE_MAX,
)
from .schemas import (
    ChecklistEvaluation,
    CriterionResult,
    LLMJudgePayload,
    LLMJudgePayload as _LLMJudgePayload,
    LLMVerdict,
)
from .versions import CHECKLIST_PROMPT_VERSION, CHECKLIST_SCHEMA_VERSION

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections import Counter
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState
from .cocoa import cocoa_alignment_check
from .faithfulness import atomic_claim_grounding


logger = logging.getLogger(__name__)


# Deterministic pre-gates (7 — pure Python, zero LLM cost)
# Each function takes the parsed sawc payload dict and returns one
# CriterionResult. Stable interface so the node can iterate them in
# a list without per-check special-casing.
#
# We accept the raw dict (not a Pydantic Section list) because the sawc
# blob is JSON-deserialized into a dict and re-parsing through Pydantic
# would add cost without benefit — these checks only read fields, no
# validation needed here (sawc_write already validated upstream).


def check_all_sections_present(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_done = int(cs.get("n_sections_completed", 0))
    n_total = int(cs.get("n_sections", 0))
    passed = (n_total > 0) and (n_done == n_total)
    return CriterionResult(
        name = "all_sections_present",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"only {n_done}/{n_total} sections completed (sawc reported "
                 f"some sections failed to write). mgsr_replan should retry "
                 f"the missing sections."
        ),
    )


def check_no_placeholder_sections(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_fb = int(cs.get("n_sections_fallback", 0))
    passed = n_fb == 0
    return CriterionResult(
        name = "no_placeholder_sections",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"{n_fb} section(s) are placeholders (all 3 writer drafts "
                 f"failed). mgsr_replan should target these specifically "
                 f"with a fresh outline + retry."
        ),
    )


def check_unique_headings(sawc: dict) -> CriterionResult:
    sections = sawc.get("sections") or []
    headings = [(s.get("heading") or "").strip().casefold() for s in sections]
    n_total = len(headings)
    n_unique = len(set(headings))
    passed = n_total == n_unique
    if passed:
        feedback = ""
    else:
        seen: set[str] = set()
        dupes: list[str] = []
        for h in headings:
            if h in seen and h not in dupes:
                dupes.append(h)
            seen.add(h)
        feedback = (
            f"duplicate section headings (case-insensitive): "
            f"{sorted(set(dupes))[:3]}. mgsr_replan should rename or merge."
        )
    return CriterionResult(
        name = "unique_headings",
        passed = passed,
        kind = "deterministic",
        feedback = feedback,
    )


def check_all_sections_cite_at_least_1(sawc: dict) -> CriterionResult:
    sections = sawc.get("sections") or []
    thin: list[str] = []
    for s in sections:
        n_cites = len(s.get("citations") or [])
        if n_cites < MIN_CITATIONS_PER_SECTION:
            thin.append(s.get("section_id", "?"))
    passed = not thin
    return CriterionResult(
        name = "all_sections_cite_at_least_1",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"sections with <{MIN_CITATIONS_PER_SECTION} citation(s): "
                 f"{thin}. add a citation grounding each section's primary "
                 f"claim."
        ),
    )


def check_density_within_bounds(sawc: dict) -> CriterionResult:
    """v2 cookbook schema (2026-05-24 PM): the writer emits 1-2 sentence
    explanations (8-80 words) BEFORE each code block. The chapter-wide
    average should land in the productive middle — too thin = under-
    contextualized code; too verbose = wall-of-text before the code."""
    cs = sawc.get("coverage_stats") or {}
    avg = float(cs.get("avg_explanation_words", 0))
    floor = DENSITY_MIN_AVG_EXPLANATION_WORDS
    ceil = DENSITY_MAX_AVG_EXPLANATION_WORDS
    passed = floor <= avg <= ceil
    if passed:
        feedback = ""
    elif avg < floor:
        feedback = (
            f"explanations are too thin ({avg:.0f} avg words; floor "
            f"{floor:.0f}). expand the 1-2 sentence lead-in BEFORE each "
            f"code block with concrete API/parameter detail."
        )
    else:
        feedback = (
            f"explanations are too verbose ({avg:.0f} avg words; ceiling "
            f"{ceil:.0f}). compress to 1-2 sentences — the code is the "
            f"point, the prose just sets it up."
        )
    return CriterionResult(
        name = "density_within_bounds",
        passed = passed,
        kind = "deterministic",
        feedback = feedback,
    )


def check_repair_rate_low(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_repairs = int(cs.get("n_repairs", 0))
    n_drafts = int(cs.get("n_total_drafts_fired", 0))
    rate = (n_repairs / n_drafts) if n_drafts else 0.0
    passed = rate < REPAIR_RATE_MAX
    return CriterionResult(
        name = "repair_rate_low",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"high writer-repair rate ({n_repairs}/{n_drafts} = "
                 f"{rate:.0%}; ceiling {REPAIR_RATE_MAX:.0%}). The writer "
                 f"struggled with Pydantic+cross-ref compliance — consider "
                 f"a clearer outline or tighter contributions."
        ),
    )


def check_picker_fallback_rate_low(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_fb = int(cs.get("n_picker_fallbacks", 0))
    n_picks = int(cs.get("n_critic_picks", 0))
    rate = (n_fb / n_picks) if n_picks else 0.0
    passed = rate < PICKER_FALLBACK_RATE_MAX
    return CriterionResult(
        name = "picker_fallback_rate_low",
        passed = passed,
        kind = "deterministic",
        feedback = (
            ""
            if passed
            else f"high critic-picker fallback rate ({n_fb}/{n_picks} = "
                 f"{rate:.0%}; ceiling {PICKER_FALLBACK_RATE_MAX:.0%}). "
                 f"the critic LLM frequently returned malformed JSON; "
                 f"the structural-score fallback handled it, but quality "
                 f"signal is degraded."
        ),
    )


def check_code_density_appropriate(sawc: dict) -> CriterionResult:
    """Code-first gate (v2 cookbook): subtopic count == code-block count.
    Passes when (a) avg subtopics/section ≥ MIN_AVG_CODE_REFS_PER_SECTION
    AND (b) ≥ MIN_CODE_REF_COVERAGE_FRACTION of allowed_hashes per section
    landed in a subtopic. Sections with no allowed_hashes are exempt from
    the coverage check; the average check still applies."""
    sections = sawc.get("sections") or []
    if not sections:
        return CriterionResult(
            name = "code_density_appropriate",
            passed = False,
            kind = "deterministic",
            feedback = "no sections — chapter is empty",
        )

    n_refs_per_section: list[tuple[str, int]] = []
    thin_coverage: list[str] = []
    n_total_refs = 0
    for s in sections:
        sid = s.get("section_id", "?")
        subtopics = s.get("subtopics") or []
        n_refs = sum(1 for st in subtopics if (st or {}).get("code_ref_hash"))
        n_total_refs += n_refs
        n_refs_per_section.append((sid, n_refs))
        n_allowed = int(s.get("n_allowed_hashes") or 0)
        if n_allowed >= 3:
            coverage = n_refs / max(1, n_allowed)
            if coverage < MIN_CODE_REF_COVERAGE_FRACTION:
                thin_coverage.append(f"{sid}({n_refs}/{n_allowed})")
    avg = n_total_refs / len(sections)
    passed = (
        avg >= MIN_AVG_CODE_REFS_PER_SECTION
        and len(thin_coverage) <= len(sections) // 2   # tolerate 50% thin
    )
    if passed:
        feedback = ""
    else:
        zeros = [sid for sid, n in n_refs_per_section if n == 0]
        feedback = (
            f"code density too low: avg {avg:.2f} subtopics/section "
            f"(floor {MIN_AVG_CODE_REFS_PER_SECTION}); "
            f"{len(zeros)} sections with 0 code subtopics"
        )
        if zeros[:5]:
            feedback += f": {zeros[:5]}"
        if thin_coverage[:5]:
            feedback += (
                f"; {len(thin_coverage)} sections under-using code bank: "
                f"{thin_coverage[:5]}"
            )
        feedback += (
            ". This is a CODE-FIRST learning resource — every section "
            "must emit ≥3 (subheading, explanation, code block) subtopics."
        )
    return CriterionResult(
        name = "code_density_appropriate",
        passed = passed,
        kind = "deterministic",
        feedback = feedback,
    )


def check_code_uniqueness_ratio(sawc: dict) -> CriterionResult:
    """Code-block uniqueness gate. Earlier corpora showed chapters with
    100s of code blocks but only ~10-20% unique bodies (one snippet
    repeated 27× across H3s). The density check doesn't catch that —
    this does.

    Adaptive floor (scales with bank diversity to avoid math-impossible
    failures):
      n_unique ≥ 30 → strict 0.50  (rich bank — writer should diversify)
      15 ≤ n_unique < 30 → 0.35    (constrained — writer used what was there)
      n_unique < 15 → 0.30         (severely constrained — signal degrades)

    Uses code_ref_hash as the uniqueness key. Excludes 'derived' subtopics
    (sawc_derive regenerates body per call → hash reuse is fine)."""
    sections = sawc.get("sections") or []
    if not sections:
        return CriterionResult(
            name = "code_uniqueness_ratio",
            passed = True,
            kind = "deterministic",
            feedback = "no sections — vacuously true",
        )

    hashes: list[str] = []
    for s in sections:
        for st in (s.get("subtopics") or []):
            if not isinstance(st, dict):
                continue
            if (st.get("code_source") or "verbatim") == "derived":
                continue
            h = st.get("code_ref_hash")
            if h:
                hashes.append(h)

    if not hashes:
        return CriterionResult(
            name = "code_uniqueness_ratio",
            passed = True,
            kind = "deterministic",
            feedback = "no verbatim code blocks — vacuously true",
        )

    n_total = len(hashes)
    n_unique = len(set(hashes))
    ratio = n_unique / n_total

    if n_unique >= 30:
        adaptive_floor = 0.50
    elif n_unique >= 15:
        adaptive_floor = 0.35
    else:
        adaptive_floor = 0.30
    passed = ratio >= adaptive_floor

    if passed:
        return CriterionResult(
            name = "code_uniqueness_ratio",
            passed = True,
            kind = "deterministic",
            feedback = "",
        )

    from collections import Counter
    top = Counter(hashes).most_common(3)
    sample = ", ".join(f"{h[:8]}…×{n}" for h, n in top if n > 1)
    feedback = (
        f"code uniqueness {ratio:.0%} ({n_unique} unique / {n_total} "
        f"total verbatim blocks); adaptive floor {adaptive_floor:.0%} "
        f"(scaled to bank diversity). Top duplicates: {sample}. "
        f"Sections are recycling the same vault snippets across "
        f"different subtopics — split overloaded sections or merge "
        f"sections that share most of their code base."
    )
    return CriterionResult(
        name = "code_uniqueness_ratio",
        passed = False,
        kind = "deterministic",
        feedback = feedback,
    )


# Ordered list — stable iteration = stable pass-rate denominators.
DETERMINISTIC_CHECKS = (
    check_all_sections_present,
    check_no_placeholder_sections,
    check_unique_headings,
    check_all_sections_cite_at_least_1,
    check_density_within_bounds,
    check_repair_rate_low,
    check_picker_fallback_rate_low,
    check_code_density_appropriate,
    check_code_uniqueness_ratio,
)


# Aggregation helpers
def aggregate_pass_rate(
    results: list[CriterionResult],
) -> tuple[int, int, float, bool]:
    """Compute (n_passed, n_total, pass_rate, chapter_passed) from
    the full criterion list."""
    n_total = len(results)
    n_passed = sum(1 for r in results if r.passed)
    pass_rate = (n_passed / n_total) if n_total else 0.0
    chapter_passed = pass_rate >= PASS_THRESHOLD
    return n_passed, n_total, pass_rate, chapter_passed


def collect_failed_feedback(results: list[CriterionResult]) -> list[str]:
    """Extract the natural-language feedback for each failed criterion,
    formatted as `[criterion_name] feedback_text`. The format makes it
    easy for mgsr_replan to parse + tag by criterion."""
    out: list[str] = []
    for r in results:
        if not r.passed and r.feedback:
            out.append(f"[{r.name}] {r.feedback}")
    return out


# Chapter rendering for the LLM-judge prompt
def render_chapter_for_judge(
    sawc: dict,
    *,
    char_cap: int = MAX_RENDERED_CHAPTER_CHARS,
) -> tuple[str, bool]:
    """Render the persisted v2 cookbook sections into a markdown-ish
    block the LLM-judge can read. Mirrors the final-render structure
    (H2 + intro + H3 subtopics) so the judge sees what the reader sees.

    Format (per section):

        ## s{N}: {heading}
        {intro}

        ### {subheading_1}
        {explanation_1}

        [code-block: {hash_prefix}…]

        ### {subheading_2}
        ...

        [citations (M): source-a.md ('claim'), source-b.md ('claim')]

    Returns (text, truncated_flag). `truncated_flag` is True when we hit
    `char_cap` and stopped concatenating remaining sections — the LLM-
    judge prompt notes this so the judge doesn't penalize "incomplete
    chapter" criteria when truncation was our doing.
    """
    parts: list[str] = []
    total = 0
    truncated = False
    sections = sawc.get("sections") or []
    for s in sections:
        sid = s.get("section_id", "?")
        heading = s.get("heading", "?")
        block_lines: list[str] = [f"## {sid}: {heading}"]
        intro = (s.get("intro") or "").strip()
        if intro:
            block_lines.append("")
            block_lines.append(intro)
        subtopics = s.get("subtopics") or []
        for st in subtopics:
            st = st or {}
            block_lines.append("")
            block_lines.append(f"### {st.get('subheading', '?')}")
            expl = (st.get("explanation") or "").strip()
            if expl:
                block_lines.append("")
                block_lines.append(expl)
            h = (st.get("code_ref_hash") or "")
            if h:
                block_lines.append("")
                block_lines.append(f"[code-block: {h[:12]}…]")
        # Compact metadata at section end
        citations = s.get("citations") or []
        if citations:
            cite_summary = "; ".join(
                f"{(c.get('source_key') or '').rsplit('/', 1)[-1]} ("
                f"'{(c.get('claim') or '')[:80]}')"
                for c in citations[:5]
            )
            block_lines.append("")
            block_lines.append(f"[citations ({len(citations)}): {cite_summary}]")
        block_lines.append("")
        block = "\n".join(block_lines)
        if total + len(block) > char_cap:
            truncated = True
            break
        parts.append(block)
        total += len(block)
    return ("\n".join(parts), truncated)


def render_digest_for_grounding(
    digest: dict,
    *,
    char_cap: int = 20_000,
) -> str:
    """Render compressed per-section contributions so the LLM-judge can
    check `claims_grounded_in_sources` without seeing the full source
    documents."""
    parts: list[str] = []
    total = 0
    per_section = digest.get("per_section") or {}
    for sid in sorted(per_section.keys()):
        contribs = per_section[sid]
        if not contribs:
            continue
        block_lines = [f"## {sid} grounding:"]
        for c in contribs[:4]:
            src = c.get("source_key", "?")
            src_short = src.rsplit("/", 1)[-1] if src else "?"
            relevance = c.get("relevance", "?")
            summ = c.get("summary", "")
            facts = c.get("key_facts") or []
            block_lines.append(
                f"  - {src_short} ({relevance}): {summ[:200]}"
            )
            for f in facts[:3]:
                block_lines.append(f"    • {f[:200]}")
        block = "\n".join(block_lines)
        if total + len(block) > char_cap:
            block_lines.append("[...truncated...]")
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


# LLM-judge prompts
# Bundle 9 (2026-05-25) — Per-criterion description block. Keys MUST be the
# `LLM_CRITERIA` names verbatim so the post-shuffle prompt template stays
# consistent. Each value is the labelled description block that appears in
# the prompt's CRITERIA section. The cN labels stay attached to their
# criterion (they're identifiers, not positional markers), and the OUTPUT
# JSON key order also follows the shuffle so the LLM's attention shape is
# uniform across runs.
_CRITERION_BLOCKS: dict[str, str] = {
    "chapter_reads_coherently": (
        "[c8] chapter_reads_coherently\n"
        "  Reading sections in order, does the chapter flow as a single "
        "document with smooth transitions, OR as disjoint reference "
        "cards with abrupt scope shifts? PASS if it reads as one "
        "document; FAIL if multiple sections feel like standalone "
        "definitions with no connective tissue."
    ),
    "claims_grounded_in_sources": (
        "[c9] claims_grounded_in_sources\n"
        "  Spot-check 3-5 citations against the per-section grounding "
        "above. Does each cited source actually back the specific claim "
        "the section makes in prose nearby? PASS if claims align with "
        "the digest's key_facts; FAIL if any cited source is being "
        "stretched beyond what it supports."
    ),
    "terminology_consistent": (
        "[c10] terminology_consistent\n"
        "  Does the chapter use the SAME name for the SAME concept "
        "across sections (e.g., not switching between 'field' and "
        "'attribute' for the same Pydantic concept, or 'method' and "
        "'function' interchangeably for the same API)? PASS if "
        "terminology is stable; FAIL if you can point to ≥2 sections "
        "using different names for the same thing."
    ),
    "prose_code_first_not_meta_framing": (
        "[c11] prose_code_first_not_meta_framing\n"
        "  Is each section's prose dense + production-focused (concrete "
        "APIs, types, parameters, error modes), OR padded with meta-"
        "framing ('In this chapter we will...', 'In summary...', 'It "
        "is important to note that...')? PASS if prose is dense; FAIL "
        "if meta-framing eats >20% of any section's `intro` or any "
        "H3 subtopic's `explanation`."
    ),
    "code_refs_introduced_in_prose": (
        "[c12] code_refs_introduced_in_prose\n"
        "  In the v2 cookbook structure, each H3 subtopic emits "
        "`{subheading} → {explanation} → [code-block]`. Does each "
        "subtopic's explanation (1-2 sentences BEFORE the code) "
        "actually introduce that specific code block — naming the "
        "decorator/type/parameter the reader is about to see — OR is "
        "it generic prose that could precede ANY code block? PASS if "
        "explanations are tied to their specific code; FAIL if any "
        "explanation reads as filler.\n"
        "  NOTE: If a section has 0 subtopics (rare — usually a "
        "placeholder), this criterion FAILS for that section. The "
        "cookbook contract requires ≥3 subtopics per section."
    ),
}


def _criterion_order_for(chapter_id: str) -> list[str]:
    """Deterministic per-chapter shuffle of the 5 LLM criteria.

    Caching contract: same chapter_id + same prompt_version → same order →
    cache hits work. Different chapters get different orders → primacy /
    recency bias averages out across the corpus (arXiv 2604.03684,
    2301.08721; LLM-as-judge order-effect mitigation).
    """
    seed_material = (
        f"{chapter_id}|{CHECKLIST_PROMPT_VERSION}".encode("utf-8")
    )
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    rng = random.Random(seed)
    order = list(LLM_CRITERIA)
    rng.shuffle(order)
    return order


def build_judge_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
) -> str:
    """Build the batched LLM-judge prompt — one call returns all 5
    semantic criteria as a single JSON object. Prometheus-2-style
    binary rubric (CheckEval evidence: +0.45 inter-evaluator agreement
    over continuous Likert).

    Bundle 9 (2026-05-25): the criterion blocks AND the output JSON key
    order are now deterministically shuffled per chapter_id to mitigate
    LLM position bias. Same chapter → same order (caching preserved);
    different chapters → different orders (bias averages out)."""
    trunc_note = (
        "\n\nNOTE: The chapter text was truncated to fit the prompt — "
        "do NOT penalize 'incomplete chapter' or 'missing sections' if "
        "the visible content reads coherently up to the truncation point."
        if truncated else ""
    )
    order = _criterion_order_for(chapter_id)
    criteria_block = "\n\n".join(_CRITERION_BLOCKS[name] for name in order)
    output_lines = ",\n".join(
        f'  {name!r:<40}: {{"passed": ..., "feedback": "..."}}'
        for name in order
    )
    return (
        f"You are the Checklist Evaluator for chapter {chapter_id} "
        f"({chapter_title!r}) of framework {framework}. Apply 5 BINARY "
        f"criteria below. Each: PASS (true) or FAIL (false). If false, "
        f"give a 1-sentence specific feedback so mgsr_replan can act "
        f"surgically (which section + what's wrong). Be strict — don't "
        f"grade-inflate; pass only what you'd defend to a peer reviewer.\n\n"

        f"== CHAPTER (sections rendered top-to-bottom) =={trunc_note}\n"
        f"{rendered_chapter}\n"
        f"== END CHAPTER ==\n\n"

        f"== PER-SECTION GROUNDING (digest summaries — what each section "
        f"SHOULD cover, sourced from the digest_construct step) ==\n"
        f"{rendered_digest}\n"
        f"== END GROUNDING ==\n\n"

        f"== CRITERIA — answer each with PASS or FAIL + 1-sentence "
        f"specific feedback if FAIL ==\n\n"
        f"{criteria_block}\n\n"

        f"OUTPUT — strict JSON, exactly these 5 keys (each value: "
        f'{{"passed": bool, "feedback": "1-sentence specific reason if '
        f'false; empty string if true"}}):\n'
        f"{{\n{output_lines}\n}}\n\n"

        f"Respond ONLY with valid JSON. NO prose commentary, NO markdown "
        f"wrapping. Feedback should name a specific section + symptom "
        f"(e.g., 's4 opens with \"In this chapter we will explore...\"' "
        f"or 's7 cites 0024-isbn.md but its claim isn't in the key_facts')."
    )


def build_repair_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
    current_json: str,
    issues: list[str],
) -> str:
    """Repair prompt when the judge's first response was Pydantic-
    invalid (missing keys / wrong shape)."""
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix the JSON output. Keep the same 5-key shape; only correct "
        f"the structural issues below.\n\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"FRAMEWORK: {framework}\n\n"
        f"CURRENT (broken) JSON:\n{current_json}\n\n"
        f"ISSUES TO FIX:\n{issues_block}\n\n"
        f"Required keys (each value = "
        f'{{"passed": bool, "feedback": str}}):\n'
        f"  - chapter_reads_coherently\n"
        f"  - claims_grounded_in_sources\n"
        f"  - terminology_consistent\n"
        f"  - prose_code_first_not_meta_framing\n"
        f"  - code_refs_introduced_in_prose\n\n"
        f"Respond ONLY with valid JSON, no commentary."
    )


# LLM verdict → CriterionResult coercion
def llm_payload_to_criteria(
    payload: LLMJudgePayload,
) -> list[CriterionResult]:
    """Map the parsed LLM judge response into 5 CriterionResult entries,
    preserving the `LLM_CRITERIA` order."""
    name_to_verdict = {
        "chapter_reads_coherently":          payload.chapter_reads_coherently,
        "claims_grounded_in_sources":        payload.claims_grounded_in_sources,
        "terminology_consistent":            payload.terminology_consistent,
        "prose_code_first_not_meta_framing": payload.prose_code_first_not_meta_framing,
        "code_refs_introduced_in_prose":     payload.code_refs_introduced_in_prose,
    }
    return [
        CriterionResult(
            name = name,
            passed = name_to_verdict[name].passed,
            kind = "llm_judge",
            feedback = (
                name_to_verdict[name].feedback
                if not name_to_verdict[name].passed
                else ""
            ),
        )
        for name in LLM_CRITERIA
    ]



# === checklist-helpers restored from old commit (2026-06-07) ===
_CRITERION_BLOCKS: dict[str, str] = {
    "chapter_reads_coherently": (
        "[c8] chapter_reads_coherently\n"
        "  Reading sections in order, does the chapter flow as a single "
        "document with smooth transitions, OR as disjoint reference "
        "cards with abrupt scope shifts? PASS if it reads as one "
        "document; FAIL if multiple sections feel like standalone "
        "definitions with no connective tissue."
    ),
    "claims_grounded_in_sources": (
        "[c9] claims_grounded_in_sources\n"
        "  Spot-check 3-5 citations against the per-section grounding "
        "above. Does each cited source actually back the specific claim "
        "the section makes in prose nearby? PASS if claims align with "
        "the digest's key_facts; FAIL if any cited source is being "
        "stretched beyond what it supports."
    ),
    "terminology_consistent": (
        "[c10] terminology_consistent\n"
        "  Does the chapter use the SAME name for the SAME concept "
        "across sections (e.g., not switching between 'field' and "
        "'attribute' for the same Pydantic concept, or 'method' and "
        "'function' interchangeably for the same API)? PASS if "
        "terminology is stable; FAIL if you can point to ≥2 sections "
        "using different names for the same thing."
    ),
    "prose_code_first_not_meta_framing": (
        "[c11] prose_code_first_not_meta_framing\n"
        "  Is each section's prose dense + production-focused (concrete "
        "APIs, types, parameters, error modes), OR padded with meta-"
        "framing ('In this chapter we will...', 'In summary...', 'It "
        "is important to note that...')? PASS if prose is dense; FAIL "
        "if meta-framing eats >20% of any section's `intro` or any "
        "H3 subtopic's `explanation`."
    ),
    "code_refs_introduced_in_prose": (
        "[c12] code_refs_introduced_in_prose\n"
        "  In the v2 cookbook structure, each H3 subtopic emits "
        "`{subheading} → {explanation} → [code-block]`. Does each "
        "subtopic's explanation (1-2 sentences BEFORE the code) "
        "actually introduce that specific code block — naming the "
        "decorator/type/parameter the reader is about to see — OR is "
        "it generic prose that could precede ANY code block? PASS if "
        "explanations are tied to their specific code; FAIL if any "
        "explanation reads as filler.\n"
        "  NOTE: If a section has 0 subtopics (rare — usually a "
        "placeholder), this criterion FAILS for that section. The "
        "cookbook contract requires ≥3 subtopics per section."
    ),
}

async def _run_llm_judge(
    *,
    thread_id: str,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
) -> tuple[list[CriterionResult], Optional[str], bool, int]:
    """Fire the batched LLM-judge call → parse → validate → repair-if-needed.

    Returns (criteria_results, deployment, was_repaired, wall_ms).
    On hard failure, returns a fallback set of FAILED verdicts so the
    chapter conservatively fails the LLM layer.
    """
    t0 = time.monotonic()
    prompt = build_judge_prompt(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework=framework,
        rendered_chapter=rendered_chapter,
        rendered_digest=rendered_digest,
        truncated=truncated,
    )

    deployment: Optional[str] = None
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_JUDGE,
            temperature=_TEMPERATURE_JUDGE,
            response_format=_JUDGE_RESPONSE_FORMAT,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[checklist_eval] LLM judge call failed: "
            f"{type(e).__name__}: {e}"
        )
        return (
            _fallback_llm_verdicts(f"{type(e).__name__}"),
            None, False, wall_ms,
        )

    parsed = _parse_json_response(response)
    payload: Optional[_LLMJudgePayload] = None
    err: Optional[str] = None
    repaired = False

    if parsed is not None:
        payload, err = _try_parse_judge(parsed)

    # One repair attempt if parse OR Pydantic failed
    if payload is None and _MAX_REPAIR_ATTEMPTS > 0:
        repair_issues = [
            err if err else "previous response was not parseable JSON"
        ]
        current_json = json.dumps(parsed or {"_raw": (response or "")[:400]})
        repair_prompt = build_repair_prompt(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=framework,
            rendered_chapter=rendered_chapter,
            rendered_digest=rendered_digest,
            truncated=truncated,
            current_json=current_json,
            issues=repair_issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                response_format=_JUDGE_RESPONSE_FORMAT,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp is not None:
                payload, err = _try_parse_judge(rp)
                if payload is not None:
                    repaired = True
        except Exception as e:
            logger.warning(
                f"[checklist_eval] LLM judge repair failed: "
                f"{type(e).__name__}: {e}"
            )

    wall_ms = int((time.monotonic() - t0) * 1000)

    if payload is None:
        logger.warning(
            f"[checklist_eval] LLM judge unparseable after repair "
            f"({err}); using fallback FAIL verdicts"
        )
        return (
            _fallback_llm_verdicts(f"judge_parse_failed: {err}"),
            deployment, False, wall_ms,
        )

    return llm_payload_to_criteria(payload), deployment, repaired, wall_ms

def _compute_manifest_hash(
    *,
    sawc_manifest_hash: str,
    digest_manifest_hash: str,
) -> str:
    payload = (
        f"sawc={sawc_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={CHECKLIST_PROMPT_VERSION}|"
        f"schema={CHECKLIST_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# === checklist round 2 helpers from old commit ===
_TEMPERATURE_JUDGE      = 0.0

_TEMPERATURE_REPAIR     = 0.0

_MAX_TOKENS_JUDGE       = 3000

_MAX_TOKENS_REPAIR      = 3000

_MAX_REPAIR_ATTEMPTS    = 1

_JUDGE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "checklist_judge",
        "schema": _LLMJudgePayload.model_json_schema(),
        "strict": False,
    },
}

def _parse_json_response(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

def _try_parse_judge(
    raw: dict,
) -> tuple[Optional[_LLMJudgePayload], Optional[str]]:
    try:
        return _LLMJudgePayload.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"

def _fallback_llm_verdicts(reason: str) -> list[CriterionResult]:
    """When the judge LLM is unreachable / malformed beyond repair,
    conservatively mark all 5 LLM criteria as failed with the reason
    as feedback. This drops chapter pass_rate to at most 7/12 = 58%
    (below the 80% threshold), so mgsr_replan will be invoked — which
    is the correct behavior when we can't verify the chapter."""
    out: list[CriterionResult] = []
    for name in _LLM_CRITERIA:
        out.append(CriterionResult(
            name=name,
            passed=False,
            kind="llm_judge",
            feedback=(
                f"judge_unavailable: {reason}. Conservatively marked "
                f"FAIL so mgsr_replan re-evaluates next iteration."
            ),
        ))
    return out


# === checklist round 3 helpers ===
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def _shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:6]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 6} more)" if len(errs) > 6 else ""
    return "; ".join(lines) + suffix

async def checklist_eval_run(state: SynthState) -> dict:
    """Run the binary checklist evaluator for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped": "no_slug_or_chapter_id", "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # -- Load sawc + digest blobs -------------------------------------------
    sawc_key = _sawc_latest_key(slug, chapter_id)
    digest_key = _digest_latest_key(slug, chapter_id)

    if not await minio.exists(sawc_key):
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped":  "sawc_not_found",
                "sawc_key": sawc_key,
                "wall_ms":  int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc {sawc_key!r} not in MinIO — run sawc_write first",
        }
    if not await minio.exists(digest_key):
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped":    "digest_not_found",
                "digest_key": digest_key,
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"digest {digest_key!r} not in MinIO — run digest_construct first",
        }

    try:
        sawc_text = await minio.read_text(sawc_key)
        sawc = json.loads(sawc_text)
        digest_text = await minio.read_text(digest_key)
        digest = json.loads(digest_text)
    except Exception as e:
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc/digest unreadable: {type(e).__name__}: {e}",
        }

    chapter_title = sawc.get("chapter_title") or chapter_id
    sawc_manifest_hash = sawc.get("sawc_manifest_hash") or ""
    digest_manifest_hash = digest.get("digest_manifest_hash") or ""

    await emit_progress(
        thread_id, "checklist_eval", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_total_criteria = len(DETERMINISTIC_CHECKS) + len(LLM_CRITERIA),
        pass_threshold = 0.80,
    )

    # -- Cache fast-path ----------------------------------------------------
    manifest_hash = _compute_manifest_hash(
        sawc_manifest_hash = sawc_manifest_hash,
        digest_manifest_hash = digest_manifest_hash,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_total":         cached.get("n_total", 0),
                "n_passed":        cached.get("n_passed", 0),
                "pass_rate":       cached.get("pass_rate", 0.0),
                "chapter_passed":  cached.get("chapter_passed", False),
                "n_failed_feedback": len(cached.get("failed_feedback") or []),
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "checklist_eval", "done",
                n_total = stats["n_total"],
                n_passed = stats["n_passed"],
                pass_rate = stats["pass_rate"],
                chapter_passed = stats["chapter_passed"],
                n_failed_feedback = stats["n_failed_feedback"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(
                f"[checklist_eval] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_passed']}/{stats['n_total']} "
                f"({stats['pass_rate']:.0%}), passed = "
                f"{stats['chapter_passed']}, {elapsed} ms"
            )
            return {"checklist_path": latest_key, "checklist_stats": stats}
        except Exception as e:
            logger.warning(
                f"[checklist_eval] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # -- Layer 1: 7 deterministic pre-gates ---------------------------------
    pre_results: list[CriterionResult] = []
    for fn in DETERMINISTIC_CHECKS:
        try:
            pre_results.append(fn(sawc))
        except Exception as e:
            # Defensive: a check shouldn't crash, but if it does we
            # surface a clear FAIL so the operator can see which one
            logger.warning(
                f"[checklist_eval] pre-gate {fn.__name__} crashed: "
                f"{type(e).__name__}: {e}"
            )
            pre_results.append(CriterionResult(
                name = fn.__name__.replace("check_", ""),
                passed = False,
                kind = "deterministic",
                feedback = f"pre_gate_crashed: {type(e).__name__}",
            ))

    pre_failed = [r.name for r in pre_results if not r.passed]
    n_pre_passed = sum(1 for r in pre_results if r.passed)
    await emit_progress(
        thread_id, "checklist_eval", "pregates_done",
        n_pregate = len(pre_results),
        n_passed = n_pre_passed,
        names_failed = pre_failed,
    )

    # -- Render chapter + digest for the LLM-judge prompt -------------------
    rendered_chapter, truncated = render_chapter_for_judge(sawc)
    rendered_digest = render_digest_for_grounding(digest)

    await emit_progress(
        thread_id, "checklist_eval", "judge_request",
        chapter_chars = len(rendered_chapter),
        digest_chars = len(rendered_digest),
        truncated = truncated,
    )

    # -- Layer 2: 1 batched LLM-judge call ----------------------------------
    llm_results, deployment, repaired, judge_wall_ms = await _run_llm_judge(
        thread_id = thread_id,
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework = slug,
        rendered_chapter = rendered_chapter,
        rendered_digest = rendered_digest,
        truncated = truncated,
    )

    llm_failed = [r.name for r in llm_results if not r.passed]
    n_llm_passed = sum(1 for r in llm_results if r.passed)
    await emit_progress(
        thread_id, "checklist_eval", "judge_done",
        n_llm = len(llm_results),
        n_passed = n_llm_passed,
        names_failed = llm_failed,
        wall_ms = judge_wall_ms,
        deployment = deployment,
        repaired = repaired,
    )

    # -- Augment: atomic-claim grounding check (2026-05-24) -----------------
    # The bundled judge above gives a coarse PASS/FAIL on
    # `claims_grounded_in_sources` based on a 3-5 citation spot-check. This
    # separate pass extracts atomic claims + verifies each against the digest
    # grounding via bandit-routed LLM calls (per-claim, parallel concurrency = 8).
    # If atomic check finds any unsupported claim, we OVERRIDE the bundled
    # judge's verdict to FAIL with specific feedback. Conservative bias:
    # never upgrades the bundled judge — only downgrades it.
    # See docs/KD-SYNTH-SOTA-2026-05-24.md §3 #2.
    # DD-SYNTH-SPEED-SOTA #B1 (2026-05-26) — Parallelize CoCoA + atomic-
    # claim grounding. Both run on the same chapter draft; they share NO
    # state (atomic uses prose+digest; CoCoA uses sawc+vault). Running
    # them concurrently via asyncio.gather drops the ~3-5 min serial path
    # to ~max(2.5, 3.5) min ≈ 30-40% on the checklist tail. Each task is
    # wrapped in its own try/except so the fail-soft semantics are
    # preserved per-result.
    async def _run_faithfulness():
        t0 = time.monotonic()
        try:
            r = await atomic_claim_grounding(
                chapter_prose = rendered_chapter,
                grounding_blob = rendered_digest,
            )
            return r, int((time.monotonic() - t0) * 1000)
        except Exception as e:
            logger.warning(
                f"[checklist_eval] atomic-claim grounding crashed: "
                f"{type(e).__name__}: {e} — skipping augmentation"
            )
            return None, int((time.monotonic() - t0) * 1000)

    async def _run_cocoa():
        t0 = time.monotonic()
        try:
            from ..render.service import _load_per_source_vaults as _load_vault
            per_source = digest.get("per_source") or []
            source_keys = sorted({
                s.get("source_key", "") for s in per_source
                if s.get("source_key")
            })
            merged_vault, _, _ = await _load_vault(minio, slug, source_keys)
            r = await cocoa_alignment_check(
                sawc_payload = sawc,
                vault = merged_vault,
            )
            return r, int((time.monotonic() - t0) * 1000)
        except Exception as e:
            logger.warning(
                f"[checklist_eval] CoCoA alignment crashed: "
                f"{type(e).__name__}: {e} — skipping augmentation"
            )
            return None, int((time.monotonic() - t0) * 1000)

    (atomic_result, faithfulness_wall_ms), (cocoa_result, cocoa_wall_ms) = (
        await asyncio.gather(_run_faithfulness(), _run_cocoa())
    )

    if atomic_result is not None and not atomic_result["passed"]:
        # Override the bundled judge's `claims_grounded_in_sources` verdict.
        # Find the entry by name and rebuild it as a failure with the
        # atomic-claim feedback. CriterionResult shape is preserved.
        for i, r in enumerate(llm_results):
            if r.name == "claims_grounded_in_sources":
                llm_results[i] = CriterionResult(
                    name = r.name,
                    passed = False,
                    kind = r.kind,
                    feedback = atomic_result["feedback"],
                )
                break
        # Recompute the pass counts for telemetry consistency.
        llm_failed = [r.name for r in llm_results if not r.passed]
        n_llm_passed = sum(1 for r in llm_results if r.passed)

    await emit_progress(
        thread_id, "checklist_eval", "faithfulness_done",
        method = (atomic_result or {}).get("method", "skipped"),
        n_claims = (atomic_result or {}).get("n_claims", 0),
        n_unsupported = (atomic_result or {}).get("n_unsupported", 0),
        overrode_bundled = (atomic_result is not None
                          and not atomic_result["passed"]),
        wall_ms = faithfulness_wall_ms,
    )

    # CoCoA two-stage code/explanation alignment override path. Augments
    # the bundled judge's c11/c12 verdicts when drift is detected. Note:
    # the cocoa_result + cocoa_wall_ms were computed above in parallel
    # with the atomic-claim check via _run_cocoa(). See arXiv 2410.03131.
    if cocoa_result is not None and not cocoa_result["passed"]:
        # CoCoA found drift — override c11 + c12. Each gets the same
        # alignment-rate-grounded feedback so mgsr_replan sees specific
        # misaligned-subtopic samples and routes the reroll surgically.
        cocoa_fb = cocoa_result["feedback"]
        for i, r in enumerate(llm_results):
            if r.name in (
                "prose_code_first_not_meta_framing",
                "code_refs_introduced_in_prose",
            ):
                llm_results[i] = CriterionResult(
                    name = r.name,
                    passed = False,
                    kind = r.kind,
                    feedback = (
                        f"[CoCoA override] {cocoa_fb}"
                        if cocoa_fb else
                        f"[CoCoA override] alignment "
                        f"{cocoa_result['alignment_rate']:.0%} below 85%"
                    ),
                )
        llm_failed = [r.name for r in llm_results if not r.passed]
        n_llm_passed = sum(1 for r in llm_results if r.passed)

    await emit_progress(
        thread_id, "checklist_eval", "cocoa_done",
        method = (cocoa_result or {}).get("method", "skipped"),
        n_pairs = (cocoa_result or {}).get("n_pairs", 0),
        n_aligned = (cocoa_result or {}).get("n_aligned", 0),
        n_misaligned = (cocoa_result or {}).get("n_misaligned", 0),
        alignment_rate = (cocoa_result or {}).get("alignment_rate", 1.0),
        overrode_bundled = (cocoa_result is not None
                          and not cocoa_result["passed"]),
        wall_ms = cocoa_wall_ms,
    )

    # -- Aggregate ----------------------------------------------------------
    all_results = list(pre_results) + list(llm_results)
    n_passed, n_total, pass_rate, chapter_passed = aggregate_pass_rate(
        all_results
    )
    failed_feedback = collect_failed_feedback(all_results)

    # -- Persist ------------------------------------------------------------
    evaluation = ChecklistEvaluation(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        criteria = all_results,
        n_passed = n_passed,
        n_total = n_total,
        pass_rate = pass_rate,
        chapter_passed = chapter_passed,
        failed_feedback = failed_feedback,
        n_llm_judge_repairs = (1 if repaired else 0),
        deployment_judge = deployment,
        wall_ms = int((time.monotonic() - t0) * 1000),
    )
    payload = evaluation.model_dump()
    payload["sawc_manifest_hash"]      = sawc_manifest_hash
    payload["digest_manifest_hash"]    = digest_manifest_hash
    payload["checklist_manifest_hash"] = manifest_hash

    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_total":            n_total,
        "n_passed":           n_passed,
        "pass_rate":          pass_rate,
        "chapter_passed":     chapter_passed,
        "n_failed_feedback":  len(failed_feedback),
        "n_pregate_passed":   n_pre_passed,
        "n_pregate_total":    len(pre_results),
        "n_llm_passed":       n_llm_passed,
        "n_llm_total":        len(llm_results),
        "names_failed":       [r.name for r in all_results if not r.passed],
        "judge_wall_ms":      judge_wall_ms,
        "judge_repaired":     repaired,
        "wall_ms":            elapsed,
        "store_path":         latest_key,
        "versioned_path":     versioned_key,
        "manifest_hash":      manifest_hash,
        "cache_hit":          False,
        "prompt_version":     CHECKLIST_PROMPT_VERSION,
        "deployment_judge":   deployment,
    }
    await emit_progress(
        thread_id, "checklist_eval", "done",
        n_total = n_total,
        n_passed = n_passed,
        pass_rate = pass_rate,
        chapter_passed = chapter_passed,
        n_failed_feedback = len(failed_feedback),
        wall_ms = elapsed,
    )
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: "
        f"{n_passed}/{n_total} criteria passed "
        f"({pass_rate:.0%}, threshold 80%, chapter_passed = {chapter_passed}); "
        f"pre = {n_pre_passed}/{len(pre_results)}, llm = {n_llm_passed}/{len(llm_results)}; "
        f"{len(failed_feedback)} feedback strings; "
        f"judge_wall = {judge_wall_ms}ms, total = {elapsed}ms"
    )
    return {"checklist_path": latest_key, "checklist_stats": stats}


# Convenience loader for downstream nodes
def load_checklist_payload(text: str) -> dict:
    """Parse the persisted checklist blob. Downstream (mgsr_replan)
    consumes `failed_feedback` + per-criterion `feedback` for guided
    refinement instructions."""
    return json.loads(text)
