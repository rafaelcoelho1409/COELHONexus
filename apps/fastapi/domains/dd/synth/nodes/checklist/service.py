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
# For best-seen promotion — needs sawc's OWN versioned-key convention,
# not checklist's (each node's versioned_blob_key hardcodes its own path
# segment). See the best-seen fix below for why this lives here.
from ..sawc.keys import versioned_blob_key as _sawc_versioned_blob_key
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
from ...runtime.observability import record_grader_dim_score
from ...runtime.progress import emit_progress
from ...state import SynthState
from .cocoa import COCOA_ENABLED, cocoa_alignment_check
from .faithfulness import atomic_claim_grounding


logger = logging.getLogger(__name__)


def _emit_criterion_scores(framework: str, criteria: list[dict | CriterionResult]) -> None:
    for criterion in criteria:
        if isinstance(criterion, CriterionResult):
            name = criterion.name
            passed = criterion.passed
        else:
            name = str((criterion or {}).get("name") or "")
            passed = bool((criterion or {}).get("passed", False))
        if not name:
            continue
        record_grader_dim_score(
            framework = framework,
            dim = name,
            score = 1.0 if passed else 0.0,
        )


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
    """Chapter-wide average explanation words must land in [DENSITY_MIN, DENSITY_MAX]."""
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
    """Avg code subtopics/section ≥ floor AND ≥ MIN_CODE_REF_COVERAGE_FRACTION of hashes used."""
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
    """Adaptive uniqueness floor (0.50/0.35/0.30 by bank size); excludes derived subtopics."""
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
    """Extract failed criteria feedback as `[criterion_name] text` for mgsr_replan."""
    out: list[str] = []
    for r in results:
        if not r.passed and r.feedback:
            out.append(f"[{r.name}] {r.feedback}")
    return out


def _water_fill_blocks(
    blocks: list[str], *, char_cap: int,
) -> tuple[str, bool]:
    """Join `blocks` within `char_cap` total chars, water-filling fairly
    across all of them instead of sequential-fill-then-stop. A naive cap
    silently drops every block after whichever one blows the budget —
    for a chapter render that means every section after some midpoint
    (chapter_reads_coherently / terminology_consistent judge the WHOLE
    chapter, so never seeing its ending biases both verdicts); for a
    digest render it means later sections' grounding facts vanish
    entirely from what claims_grounded_in_sources gets to check against.
    Same algorithm as outline_sdp's source concatenation / sawc_write's
    vault-bank fix — order preserved, no entry ever fully zeroed out."""
    n = len(blocks)
    if n == 0:
        return "", False
    alloc = [0] * n
    pending = list(range(n))
    remaining_budget = char_cap
    while pending and remaining_budget > 0:
        share = remaining_budget // len(pending)
        if share <= 0:
            break
        still_pending: list[int] = []
        for i in pending:
            need = len(blocks[i]) - alloc[i]
            take = min(need, share)
            alloc[i] += take
            remaining_budget -= take
            if alloc[i] < len(blocks[i]):
                still_pending.append(i)
        pending = still_pending
    truncated = any(alloc[i] < len(blocks[i]) for i in range(n))
    parts = [blocks[i][: alloc[i]] for i in range(n) if alloc[i] > 0]
    return "\n".join(parts), truncated


def render_chapter_for_judge(
    sawc: dict,
    *,
    char_cap: int = MAX_RENDERED_CHAPTER_CHARS,
) -> tuple[str, bool]:
    """Render v2 cookbook sections for the LLM-judge; returns (text, truncated_flag)."""
    sections = sawc.get("sections") or []
    blocks: list[str] = []
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
        blocks.append("\n".join(block_lines))
    return _water_fill_blocks(blocks, char_cap = char_cap)


def render_digest_for_grounding(
    digest: dict,
    *,
    char_cap: int = 20_000,
) -> str:
    """Render compressed per-section digest contributions for the grounding judge."""
    per_section = digest.get("per_section") or {}
    blocks: list[str] = []
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
        blocks.append("\n".join(block_lines))
    text, _truncated = _water_fill_blocks(blocks, char_cap = char_cap)
    return text


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
    """Deterministic per-chapter shuffle; same chapter_id → same order (cache-safe) + bias averages out."""
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
    """Build the batched LLM-judge prompt with per-chapter criterion shuffle (position-bias mitigation)."""
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


def llm_payload_to_criteria(
    payload: LLMJudgePayload,
) -> list[CriterionResult]:
    """Map LLM judge payload → 5 CriterionResult entries in LLM_CRITERIA order."""
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



async def _run_llm_judge(
    *,
    thread_id: str,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    sawc: dict,
    digest: dict,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
) -> tuple[list[CriterionResult], Optional[str], bool, int]:
    """Fire batched judge → parse → repair if needed; hard failure → conservative FAILED fallback."""
    t0 = time.monotonic()
    cur_rendered_chapter = rendered_chapter
    cur_rendered_digest = rendered_digest
    cur_truncated = truncated

    deployment: Optional[str] = None
    response: Optional[str] = None
    last_error: Optional[Exception] = None
    for call_attempt in range(_MAX_CALL_ATTEMPTS):
        prompt = build_judge_prompt(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=framework,
            rendered_chapter=cur_rendered_chapter,
            rendered_digest=cur_rendered_digest,
            truncated=cur_truncated,
        )
        try:
            response, meta = await chat_judge_bandit_async(
                prompt,
                max_tokens=_MAX_TOKENS_JUDGE,
                temperature=_TEMPERATURE_JUDGE,
                response_format=_JUDGE_RESPONSE_FORMAT,
                timeout_s=_TIMEOUT_S_JUDGE,
            )
            deployment = (meta or {}).get("deployment")
            last_error = None
            break
        except Exception as e:
            last_error = e
            if call_attempt < _MAX_CALL_ATTEMPTS - 1:
                # The Rotator's own cascade already exhausted — a retry
                # mostly helps against a transient whole-pool wave. If
                # this looks like context overflow (60K chars ≈ 15K
                # tokens can still exceed a small-context arm from the
                # heterogeneous pool), re-render at half budget first so
                # the retry doesn't just reproduce the same failure —
                # cheap insurance against triggering a full mgsr_replan
                # cycle for what was really an infra hiccup.
                if _is_context_overflow_error(e):
                    cur_rendered_chapter, cur_truncated = (
                        render_chapter_for_judge(
                            sawc,
                            char_cap=MAX_RENDERED_CHAPTER_CHARS // 2,
                        )
                    )
                    cur_rendered_digest = render_digest_for_grounding(
                        digest, char_cap=10_000,
                    )
                await asyncio.sleep(1.0 + random.random())
    if last_error is not None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[checklist_eval] LLM judge call failed after "
            f"{_MAX_CALL_ATTEMPTS} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        return (
            _fallback_llm_verdicts(f"{type(last_error).__name__}"),
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
            rendered_chapter=cur_rendered_chapter,
            rendered_digest=cur_rendered_digest,
            truncated=cur_truncated,
            current_json=current_json,
            issues=repair_issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                response_format=_JUDGE_RESPONSE_FORMAT,
                timeout_s=_TIMEOUT_S_REPAIR,
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
            # 2026-09-08: `deployment` was still holding the main call's
            # value here (set at line ~733, before repair was even needed)
            # — so a repair-call timeout fell through to the fallback-FAIL
            # return below with a non-None deployment, making
            # `judge_call_failed = deployment is None` wrongly False.
            # Confirmed live on the ch-05 retest: this exact shape (main
            # call unparseable, repair call times out) recorded a pure
            # infra failure as a genuine content judgment, undermining both
            # the no-recovery-floor RETHINK protection and the
            # consecutive_infra_degraded sustained-outage counter (issue
            # #14's whole mechanism). The main-call-failure path a few
            # lines up already returns None correctly — this mirrors it.
            deployment = None

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


_TEMPERATURE_JUDGE      = 0.0

_TEMPERATURE_REPAIR     = 0.0

_MAX_TOKENS_JUDGE       = 3000

_MAX_TOKENS_REPAIR      = 3000

# chat_judge_bandit_async's own default (30s) was undersized — same fix
# as outline/digest/sawc (2026-09-06/07). A failed bundled-judge call
# here directly feeds `infra_degraded` (issues #10/#14), so a timeout
# that would have succeeded with more headroom was actively corrupting
# the sustained-outage signal, not just costing one bad iteration.
_TIMEOUT_S_JUDGE        = 90.0
_TIMEOUT_S_REPAIR       = 90.0

_MAX_REPAIR_ATTEMPTS    = 1

# Issue #22 (2026-09-08): the two result-persistence writes below have no
# bounded timeout and no log line either side — during the ch-05 retest,
# checklist_eval went silent for 20+ minutes with no trace of where it was
# stuck, coinciding with a real MinIO/network degradation window (Langfuse
# + Alloy exporters were also timing out at the same time). A stuck write
# here should fail loud and bounded, not hang indefinitely and invisibly.
_TIMEOUT_S_PERSIST_WRITE = 60.0

# Draft-call attempts before falling back to the conservative all-FAIL
# verdict (which triggers a full mgsr_replan cycle) — same idiom as
# outline_sdp/digest_construct/sawc_write's context-overflow retry.
_MAX_CALL_ATTEMPTS = 2

_CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)


def _is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same classifier idiom used across the
    synth pipeline. The Rotator is a universal gateway with no context
    -length-aware arm filtering, so the rendered chapter (up to
    MAX_RENDERED_CHAPTER_CHARS) can still exceed a small-context arm."""
    msg = str(e).lower()
    return any(marker in msg for marker in _CONTEXT_OVERFLOW_MARKERS)


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
    """Conservatively fail all 5 LLM criteria when judge is unavailable (triggers mgsr_replan)."""
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
                "failed_feedback":   cached.get("failed_feedback") or [],
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
                "infra_degraded":  False,   # a cached result is a completed prior run
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
            _emit_criterion_scores(slug, cached.get("criteria") or [])
            # Same immediate best-seen promotion as the fresh-compute path
            # below, including the issue #12 tie-breaker — a cache hit is
            # still this iteration's real score. n_pregate_passed isn't a
            # top-level field on the persisted blob, so recount it from
            # the cached criteria list (kind == "deterministic").
            _incoming_best_score = state.get("best_seen_score")
            _incoming_best_pregate = state.get("best_seen_pregate")
            _best_seen_score = _incoming_best_score
            _best_seen_pregate = _incoming_best_pregate
            _best_seen_sawc_path = state.get("best_seen_sawc_path")
            _n_pregate_passed = sum(
                1 for c in (cached.get("criteria") or [])
                if c.get("kind") == "deterministic" and c.get("passed")
            )
            _is_better = (
                _incoming_best_score is None
                or (stats["pass_rate"], _n_pregate_passed) > (
                    _incoming_best_score, _incoming_best_pregate or 0,
                )
            )
            if _is_better:
                _best_seen_score = stats["pass_rate"]
                _best_seen_pregate = _n_pregate_passed
                _best_seen_sawc_path = _sawc_versioned_blob_key(
                    slug, chapter_id, sawc_manifest_hash,
                )
            return {
                "checklist_path": latest_key,
                "checklist_stats": stats,
                # A cache hit is a completed prior run, not a live outage — reset the streak.
                "consecutive_infra_degraded": 0,
                "best_seen_score": _best_seen_score,
                "best_seen_pregate": _best_seen_pregate,
                "best_seen_sawc_path": _best_seen_sawc_path,
            }
        except Exception as e:
            logger.warning(
                f"[checklist_eval] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    pre_results: list[CriterionResult] = []
    for fn in DETERMINISTIC_CHECKS:
        try:
            pre_results.append(fn(sawc))
        except Exception as e:
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

    rendered_chapter, truncated = render_chapter_for_judge(sawc)
    rendered_digest = render_digest_for_grounding(digest)

    await emit_progress(
        thread_id, "checklist_eval", "judge_request",
        chapter_chars = len(rendered_chapter),
        digest_chars = len(rendered_digest),
        truncated = truncated,
    )

    llm_results, deployment, repaired, judge_wall_ms = await _run_llm_judge(
        thread_id = thread_id,
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework = slug,
        sawc = sawc,
        digest = digest,
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

    # CoCoA + atomic-claim grounding run in parallel (no shared state); conservative-bias: never upgrades bundled judge.
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
        if not COCOA_ENABLED:
            # Issue #20 follow-up (2026-09-08): the disable flag lives
            # inside cocoa_alignment_check, but this caller was still
            # doing the full MinIO vault-load BEFORE ever reaching that
            # check — paying (and, twice observed live on ch-01/ch-05
            # retests, sometimes hanging/crashing on) the exact cost the
            # flag was meant to avoid. Skip the load entirely while
            # disabled.
            return None, int((time.monotonic() - t0) * 1000)
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
        resolved = (atomic_result or {}).get("resolved", False),
        n_claims = (atomic_result or {}).get("n_claims", 0),
        n_evaluated = (atomic_result or {}).get("n_evaluated", 0),
        n_unsupported = (atomic_result or {}).get("n_unsupported", 0),
        n_judge_call_failures = (
            (atomic_result or {}).get("n_judge_call_failures", 0)
        ),
        overrode_bundled = (atomic_result is not None
                          and not atomic_result["passed"]),
        wall_ms = faithfulness_wall_ms,
    )

    # CoCoA found drift → override c11+c12 (arXiv 2410.03131).
    if cocoa_result is not None and not cocoa_result["passed"]:
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
        resolved = (cocoa_result or {}).get("resolved", False),
        n_pairs = (cocoa_result or {}).get("n_pairs", 0),
        n_judged = (cocoa_result or {}).get("n_judged", 0),
        n_aligned = (cocoa_result or {}).get("n_aligned", 0),
        n_misaligned = (cocoa_result or {}).get("n_misaligned", 0),
        alignment_rate = (cocoa_result or {}).get("alignment_rate", 1.0),
        overrode_bundled = (cocoa_result is not None
                          and not cocoa_result["passed"]),
        wall_ms = cocoa_wall_ms,
    )

    all_results = list(pre_results) + list(llm_results)
    n_passed, n_total, pass_rate, chapter_passed = aggregate_pass_rate(
        all_results
    )
    failed_feedback = collect_failed_feedback(all_results)

    # Best-seen promotion happens HERE, immediately, not one iteration
    # later in sawc_write's own (redundant, one-step-behind) copy of this
    # comparison. Fixed 2026-09-06 — sawc_write only ever compared the
    # PREVIOUS iteration's score at the START of the NEXT call, which
    # never runs if THIS iteration is the one the graph halts on.
    # Confirmed live: iteration 2 scored 57% vs iteration 1's 29%, but the
    # graph halted right after iteration 2's own mgsr_replan — iteration
    # 2's better score never got a chance to be promoted, and the WORSE
    # iteration 1 content shipped as "best-seen" instead. Doing it here
    # means the current iteration's own fresh score is already reflected
    # in state before _route_after_mgsr makes its halt/loop decision.
    # Fixed 2026-09-06 (issue #12) — comparing on pass_rate alone can't
    # tell "genuinely tied quality" apart from "tied only because a judge
    # call failure happened to erase a real structural advantage."
    # Confirmed live: an iteration with the best structural score all
    # chapter (n_pregate_passed) and the only one to write every section
    # tied on overall pass_rate with two earlier, less-complete iterations
    # purely because its OWN bundled judge call failed outright (llm=0/5).
    # A strict pass_rate comparison keeps the earlier (worse) entry on a
    # tie. n_pregate_passed is a deterministic, judge-independent signal
    # (unaffected by a judge call failing that round), so it's the right
    # tie-breaker: prefer more genuinely-verified structural completeness
    # when the headline score doesn't discriminate.
    incoming_best_score = state.get("best_seen_score")
    incoming_best_pregate = state.get("best_seen_pregate")
    best_seen_score = incoming_best_score
    best_seen_pregate = incoming_best_pregate
    best_seen_sawc_path = state.get("best_seen_sawc_path")
    is_better = (
        incoming_best_score is None
        or (pass_rate, n_pre_passed) > (
            incoming_best_score, incoming_best_pregate or 0,
        )
    )
    if is_better:
        best_seen_score = pass_rate
        best_seen_pregate = n_pre_passed
        best_seen_sawc_path = _sawc_versioned_blob_key(
            slug, chapter_id, sawc_manifest_hash,
        )

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
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: persisting evaluation "
        f"({len(blob_bytes)} bytes) to MinIO"
    )
    try:
        await asyncio.wait_for(
            minio.write(
                versioned_key, blob_bytes, content_type = "application/json",
            ),
            timeout = _TIMEOUT_S_PERSIST_WRITE,
        )
        await asyncio.wait_for(
            minio.write(
                latest_key, blob_bytes, content_type = "application/json",
            ),
            timeout = _TIMEOUT_S_PERSIST_WRITE,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[checklist_eval] {slug}/{chapter_id}: evaluation persist "
            f"timed out after {_TIMEOUT_S_PERSIST_WRITE}s (issue #22) — "
            f"MinIO likely degraded; re-raising"
        )
        raise
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: evaluation persisted"
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    # A low pass_rate driven by the judge infra itself failing (not by the
    # judge reviewing the chapter and rejecting it) is a different signal
    # than genuine content quality — mgsr's no-recovery short-circuit reads
    # this to decide whether skipping a RETHINK loop is actually justified.
    judge_call_failed = deployment is None
    # Fixed 2026-09-06 — infra_degraded previously only looked at
    # checklist's OWN call health (judge/cocoa/atomic-claim), completely
    # missing sawc_write's independently-timing-out writer calls. Confirmed
    # live across 3 consecutive chapters the same day: a chapter whose
    # writer failed every draft attempt with APITimeoutError still read
    # infra_degraded=False whenever checklist's own judge happened to get
    # a response that round — causing a chapter run to burn the full
    # 5-iteration budget (~33 min) instead of halting early (~16 min), and
    # separately causing genuine infra-driven total failures to skip
    # straight to HALT no-recovery with zero RETHINK attempts.
    # "parse_failed"/"pydantic_fail" are excluded — those are genuine
    # model-output-quality issues, not infra; everything else in
    # sawc_stats.error_breakdown is by construction an exception class
    # name from a failed LLM call (timeout, rate limit, context overflow,
    # server error, ...).
    sawc_error_breakdown = (state.get("sawc_stats") or {}).get("error_breakdown") or {}
    sawc_writer_degraded = any(
        kind not in ("parse_failed", "pydantic_fail")
        for kind in sawc_error_breakdown
    )
    infra_degraded = (
        judge_call_failed
        or sawc_writer_degraded
        or (atomic_result is not None and atomic_result.get("resolved") is False)
        or (cocoa_result is not None and cocoa_result.get("resolved") is False)
    )
    # Sustained-outage streak: how many RETHINK iterations IN A ROW,
    # ending with this one, were infra-degraded. Resets to 0 the moment an
    # iteration genuinely gets judged (even if it fails on content quality).
    # graph._route_after_mgsr halts early once this crosses a threshold,
    # instead of burning the full refine budget against an outage that
    # isn't clearing (confirmed live: 3+ consecutive degraded iterations
    # produced zero improvement before this counter existed).
    consecutive_infra_degraded = (
        int(state.get("consecutive_infra_degraded") or 0) + 1
        if infra_degraded else 0
    )
    stats = {
        "n_total":            n_total,
        "n_passed":           n_passed,
        "pass_rate":          pass_rate,
        "chapter_passed":     chapter_passed,
        "n_failed_feedback":  len(failed_feedback),
        # The actual strings, not just the count — sawc_write's RETHINK
        # iteration reads this to close the self-refine loop (previously
        # a RETHINK reran blind with zero signal about what was wrong).
        "failed_feedback":    failed_feedback,
        "n_pregate_passed":   n_pre_passed,
        "n_pregate_total":    len(pre_results),
        "n_llm_passed":       n_llm_passed,
        "n_llm_total":        len(llm_results),
        "names_failed":       [r.name for r in all_results if not r.passed],
        "judge_wall_ms":      judge_wall_ms,
        "judge_repaired":     repaired,
        "judge_call_failed":  judge_call_failed,
        "infra_degraded":     infra_degraded,
        "sawc_writer_degraded": sawc_writer_degraded,
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
    _emit_criterion_scores(slug, all_results)
    return {
        "checklist_path": latest_key,
        "checklist_stats": stats,
        # Top-level (not nested in checklist_stats) — graph._route_after_mgsr
        # reads SynthState fields directly, same as refine_iter/best_seen_score.
        "consecutive_infra_degraded": consecutive_infra_degraded,
        "best_seen_score": best_seen_score,
        "best_seen_pregate": best_seen_pregate,
        "best_seen_sawc_path": best_seen_sawc_path,
    }


def load_checklist_payload(text: str) -> dict:
    """Parse the persisted checklist blob."""
    return json.loads(text)
