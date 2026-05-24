"""checklist_eval service — deterministic pre-gates, aggregation, rendering,
prompt builders, and LLM verdict coercion."""
from __future__ import annotations

from .constants import (
    _DENSITY_MAX_CHARS_PER_PARA,
    _DENSITY_MIN_CHARS_PER_PARA,
    _LLM_CRITERIA,
    _MAX_RENDERED_CHAPTER_CHARS,
    _MIN_CITATIONS_PER_SECTION,
    _PASS_THRESHOLD,
    _PICKER_FALLBACK_RATE_MAX,
    _REPAIR_RATE_MAX,
)
from .types import CriterionResult, _LLMJudgePayload


# =============================================================================
# Deterministic pre-gates (7 — pure Python, zero LLM cost)
# =============================================================================
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
        name="all_sections_present",
        passed=passed,
        kind="deterministic",
        feedback=(
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
        name="no_placeholder_sections",
        passed=passed,
        kind="deterministic",
        feedback=(
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
        name="unique_headings",
        passed=passed,
        kind="deterministic",
        feedback=feedback,
    )


def check_all_sections_cite_at_least_1(sawc: dict) -> CriterionResult:
    sections = sawc.get("sections") or []
    thin: list[str] = []
    for s in sections:
        n_cites = len(s.get("citations") or [])
        if n_cites < _MIN_CITATIONS_PER_SECTION:
            thin.append(s.get("section_id", "?"))
    passed = not thin
    return CriterionResult(
        name="all_sections_cite_at_least_1",
        passed=passed,
        kind="deterministic",
        feedback=(
            ""
            if passed
            else f"sections with <{_MIN_CITATIONS_PER_SECTION} citation(s): "
                 f"{thin}. add a citation grounding each section's primary "
                 f"claim."
        ),
    )


def check_density_within_bounds(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    avg = float(cs.get("avg_chars_per_paragraph", 0))
    passed = _DENSITY_MIN_CHARS_PER_PARA <= avg <= _DENSITY_MAX_CHARS_PER_PARA
    if passed:
        feedback = ""
    elif avg < _DENSITY_MIN_CHARS_PER_PARA:
        feedback = (
            f"paragraphs are too thin ({avg:.0f} avg chars; floor "
            f"{_DENSITY_MIN_CHARS_PER_PARA}). expand prose with concrete "
            f"examples and API details."
        )
    else:
        feedback = (
            f"paragraphs are too long ({avg:.0f} avg chars; ceiling "
            f"{_DENSITY_MAX_CHARS_PER_PARA}). split run-on paragraphs."
        )
    return CriterionResult(
        name="density_within_bounds",
        passed=passed,
        kind="deterministic",
        feedback=feedback,
    )


def check_repair_rate_low(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_repairs = int(cs.get("n_repairs", 0))
    n_drafts = int(cs.get("n_total_drafts_fired", 0))
    rate = (n_repairs / n_drafts) if n_drafts else 0.0
    passed = rate < _REPAIR_RATE_MAX
    return CriterionResult(
        name="repair_rate_low",
        passed=passed,
        kind="deterministic",
        feedback=(
            ""
            if passed
            else f"high writer-repair rate ({n_repairs}/{n_drafts} = "
                 f"{rate:.0%}; ceiling {_REPAIR_RATE_MAX:.0%}). The writer "
                 f"struggled with Pydantic+cross-ref compliance — consider "
                 f"a clearer outline or tighter contributions."
        ),
    )


def check_picker_fallback_rate_low(sawc: dict) -> CriterionResult:
    cs = sawc.get("coverage_stats") or {}
    n_fb = int(cs.get("n_picker_fallbacks", 0))
    n_picks = int(cs.get("n_critic_picks", 0))
    rate = (n_fb / n_picks) if n_picks else 0.0
    passed = rate < _PICKER_FALLBACK_RATE_MAX
    return CriterionResult(
        name="picker_fallback_rate_low",
        passed=passed,
        kind="deterministic",
        feedback=(
            ""
            if passed
            else f"high critic-picker fallback rate ({n_fb}/{n_picks} = "
                 f"{rate:.0%}; ceiling {_PICKER_FALLBACK_RATE_MAX:.0%}). "
                 f"the critic LLM frequently returned malformed JSON; "
                 f"the structural-score fallback handled it, but quality "
                 f"signal is degraded."
        ),
    )


def check_code_density_appropriate(sawc: dict) -> CriterionResult:
    """Ship #3 (2026-05-24) — code-first gate.

    The chapter passes when the average code_refs per section is ≥
    _MIN_AVG_CODE_REFS_PER_SECTION (default 2.0) AND when the writer
    actually used the code bank (at least _MIN_CODE_REF_COVERAGE_FRACTION
    of the allowed_hashes available to each section ended up cited).

    Sections with zero allowed_hashes are exempt from the coverage check
    (concept-only sections don't have code to cite). The average check
    still applies — if MOST sections have no code, the chapter is too
    prose-heavy regardless.

    Failure feedback names the offending sections so mgsr_replan can
    decide whether to instruct sawc to re-roll those sections specifically
    or accept the chapter as-is.
    """
    from .constants import (
        _MIN_AVG_CODE_REFS_PER_SECTION,
        _MIN_CODE_REF_COVERAGE_FRACTION,
    )
    sections = sawc.get("sections") or []
    if not sections:
        return CriterionResult(
            name="code_density_appropriate",
            passed=False,
            kind="deterministic",
            feedback="no sections — chapter is empty",
        )

    n_refs_per_section: list[tuple[str, int]] = []
    thin_coverage: list[str] = []
    n_total_refs = 0
    for s in sections:
        sid = s.get("section_id", "?")
        n_refs = len(s.get("code_refs") or [])
        n_total_refs += n_refs
        n_refs_per_section.append((sid, n_refs))
        # Coverage check uses the allowed_hashes_count if recorded.
        n_allowed = int(s.get("n_allowed_hashes") or 0)
        if n_allowed >= 3:
            coverage = n_refs / max(1, n_allowed)
            if coverage < _MIN_CODE_REF_COVERAGE_FRACTION:
                thin_coverage.append(
                    f"{sid}({n_refs}/{n_allowed})"
                )
    avg = n_total_refs / len(sections)
    passed = (
        avg >= _MIN_AVG_CODE_REFS_PER_SECTION
        and len(thin_coverage) <= len(sections) // 2  # tolerate 50% thin
    )
    if passed:
        feedback = ""
    else:
        zeros = [sid for sid, n in n_refs_per_section if n == 0]
        feedback = (
            f"code density too low: avg {avg:.2f} code_refs/section "
            f"(floor {_MIN_AVG_CODE_REFS_PER_SECTION}); "
            f"{len(zeros)} sections with 0 code_refs"
        )
        if zeros[:5]:
            feedback += f": {zeros[:5]}"
        if thin_coverage[:5]:
            feedback += (
                f"; {len(thin_coverage)} sections under-using code bank: "
                f"{thin_coverage[:5]}"
            )
        feedback += (
            ". This is a CODE-FIRST learning resource — sections must "
            "lead with code, not summarize concepts in prose."
        )
    return CriterionResult(
        name="code_density_appropriate",
        passed=passed,
        kind="deterministic",
        feedback=feedback,
    )


# Ordered list used by the node — stable iteration order = stable
# pass-rate denominators across runs.
DETERMINISTIC_CHECKS = (
    check_all_sections_present,
    check_no_placeholder_sections,
    check_unique_headings,
    check_all_sections_cite_at_least_1,
    check_density_within_bounds,
    check_repair_rate_low,
    check_picker_fallback_rate_low,
    check_code_density_appropriate,
)


# =============================================================================
# Aggregation helpers
# =============================================================================
def aggregate_pass_rate(
    results: list[CriterionResult],
) -> tuple[int, int, float, bool]:
    """Compute (n_passed, n_total, pass_rate, chapter_passed) from
    the full criterion list."""
    n_total = len(results)
    n_passed = sum(1 for r in results if r.passed)
    pass_rate = (n_passed / n_total) if n_total else 0.0
    chapter_passed = pass_rate >= _PASS_THRESHOLD
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


# =============================================================================
# Chapter rendering for the LLM-judge prompt
# =============================================================================
def render_chapter_for_judge(
    sawc: dict,
    *,
    char_cap: int = _MAX_RENDERED_CHAPTER_CHARS,
) -> tuple[str, bool]:
    """Render the persisted ChapterDraft sections into a markdown-ish
    block the LLM-judge can read.

    Format (per section):

        ## s{N}: {heading}
        {paragraph 1}

        {paragraph 2}
        ...

        [code-refs (N): hashes hint=hint, ...]
        [citations (M): source-a.md ('claim text'), source-b.md ('claim')]

    Returns (text, truncated_flag). `truncated_flag` is True when we
    hit `char_cap` and stopped concatenating remaining sections — the
    LLM-judge prompt will note this so the judge doesn't penalize
    "incomplete chapter" criteria when the truncation was our doing.
    """
    parts: list[str] = []
    total = 0
    truncated = False
    sections = sawc.get("sections") or []
    for s in sections:
        sid = s.get("section_id", "?")
        heading = s.get("heading", "?")
        block_lines: list[str] = [f"## {sid}: {heading}"]
        for para in (s.get("paragraphs") or []):
            block_lines.append("")
            block_lines.append(para.strip())
        # Compact metadata at section end
        code_refs = s.get("code_refs") or []
        if code_refs:
            hash_summary = ", ".join(
                f"{c.get('hash', '?')[:8]}…(hint={c.get('placement_hint', '')[:30]})"
                for c in code_refs[:8]
            )
            block_lines.append("")
            block_lines.append(f"[code-refs ({len(code_refs)}): {hash_summary}]")
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


# =============================================================================
# LLM-judge prompts
# =============================================================================
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
    over continuous Likert)."""
    trunc_note = (
        "\n\nNOTE: The chapter text was truncated to fit the prompt — "
        "do NOT penalize 'incomplete chapter' or 'missing sections' if "
        "the visible content reads coherently up to the truncation point."
        if truncated else ""
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

        f"[c8] chapter_reads_coherently\n"
        f"  Reading sections in order, does the chapter flow as a single "
        f"document with smooth transitions, OR as disjoint reference "
        f"cards with abrupt scope shifts? PASS if it reads as one "
        f"document; FAIL if multiple sections feel like standalone "
        f"definitions with no connective tissue.\n\n"

        f"[c9] claims_grounded_in_sources\n"
        f"  Spot-check 3-5 citations against the per-section grounding "
        f"above. Does each cited source actually back the specific claim "
        f"the section makes in prose nearby? PASS if claims align with "
        f"the digest's key_facts; FAIL if any cited source is being "
        f"stretched beyond what it supports.\n\n"

        f"[c10] terminology_consistent\n"
        f"  Does the chapter use the SAME name for the SAME concept "
        f"across sections (e.g., not switching between 'field' and "
        f"'attribute' for the same Pydantic concept, or 'method' and "
        f"'function' interchangeably for the same API)? PASS if "
        f"terminology is stable; FAIL if you can point to ≥2 sections "
        f"using different names for the same thing.\n\n"

        f"[c11] prose_code_first_not_meta_framing\n"
        f"  Is the prose dense + production-focused (concrete APIs, "
        f"types, parameters, error modes), OR padded with meta-framing "
        f"('In this chapter we will...', 'In summary...', 'It is "
        f"important to note that...')? PASS if prose is dense; FAIL if "
        f"meta-framing eats >20% of any section.\n\n"

        f"[c12] code_refs_introduced_in_prose\n"
        f"  Where code references (`[code-refs (...)]` markers) appear, "
        f"is the surrounding prose introducing them (explaining what "
        f"the code does, why it's relevant, parameters that matter), "
        f"OR are they dumped at section end with no contextualization? "
        f"PASS if code is introduced; FAIL if code-refs appear without "
        f"prose lead-in.\n"
        f"  NOTE: If a section has 0 code-refs (the source has no code), "
        f"this criterion PASSES trivially for that section. Only fail "
        f"if at least one section has code-refs AND no prose lead-in.\n\n"

        f"OUTPUT — strict JSON, exactly these 5 keys (each value: "
        f'{{"passed": bool, "feedback": "1-sentence specific reason if '
        f'false; empty string if true"}}):\n'
        f"{{\n"
        f'  "chapter_reads_coherently":          {{"passed": ..., "feedback": "..."}},\n'
        f'  "claims_grounded_in_sources":        {{"passed": ..., "feedback": "..."}},\n'
        f'  "terminology_consistent":            {{"passed": ..., "feedback": "..."}},\n'
        f'  "prose_code_first_not_meta_framing": {{"passed": ..., "feedback": "..."}},\n'
        f'  "code_refs_introduced_in_prose":     {{"passed": ..., "feedback": "..."}}\n'
        f"}}\n\n"

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


# =============================================================================
# LLM verdict → CriterionResult coercion
# =============================================================================
def llm_payload_to_criteria(
    payload: _LLMJudgePayload,
) -> list[CriterionResult]:
    """Map the parsed LLM judge response into 5 CriterionResult entries,
    preserving the `_LLM_CRITERIA` order."""
    name_to_verdict = {
        "chapter_reads_coherently":          payload.chapter_reads_coherently,
        "claims_grounded_in_sources":        payload.claims_grounded_in_sources,
        "terminology_consistent":            payload.terminology_consistent,
        "prose_code_first_not_meta_framing": payload.prose_code_first_not_meta_framing,
        "code_refs_introduced_in_prose":     payload.code_refs_introduced_in_prose,
    }
    return [
        CriterionResult(
            name=name,
            passed=name_to_verdict[name].passed,
            kind="llm_judge",
            feedback=(
                name_to_verdict[name].feedback
                if not name_to_verdict[name].passed
                else ""
            ),
        )
        for name in _LLM_CRITERIA
    ]
