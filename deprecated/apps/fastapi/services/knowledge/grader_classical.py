"""
Knowledge Distiller — Classical (Deterministic) Grader (Phase 1.1, 2026-05-13)

Replaces the LLM grader (GRADER_PROMPT in schemas/knowledge/prompts.py) with
per-dimension deterministic scorers + 1 small-LLM call for the irreducible
`market_analysis` dimension. Designed as a drop-in replacement returning the
same `GraderEvaluation` shape so the Self-Refine loop, `keep_best` argmax,
and downstream artifacts (`evaluation.json`) work unchanged.

Pattern source: `KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` Phase 1 / Step B.
Empirical justification: the existing `_deterministic_grader_gates` helper
already proves classical pre-checks beat the LLM on `citation_integrity` and
`code_density` — this module generalizes that pattern to all 9 dimensions.

Phase 1.1 ships (this file):
  - signal_to_noise — intro-phrase regex blacklist + prose/code line ratio
  - citation_integrity — `# docs:` count vs chapter.assigned_files
  - code_density — fence-aware code/total line ratio
  - job_alignment — substring match on user_profile.target_markets
  - portfolio_synergy — substring match on user_profile.portfolio_refs
  - code_preservation_ratio — passthrough (already deterministic upstream)
  - assumption_match — STUB (Phase 1.2: ModernBERT-NLI)
  - complexity_appropriate — STUB (Phase 1.2: textstat readability)
  - market_analysis — STUB (Phase 1.2: small LLM via kd-reduce-label rotator)

Phase 1.2 will add the three STUB dims via `textstat`, `transformers`
(ModernBERT-NLI), and the existing `build_reduce_label_chain()` rotator.

Phase 1.3 wires this into `helpers.py::_grade_attempt` behind
`KD_USE_CLASSICAL_GRADER=1` env flag and adds the side-by-side
`/debug/grader_compare` endpoint.

Action rule (mirrors `GRADER_PROMPT` lines 631-636):
    composite >= acceptance_threshold       → "accept"
    0.60 <= composite < acceptance_threshold → "refine"  (issues are localized)
    composite < 0.60                         → "regenerate" (structural failure)
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from schemas.knowledge.agents import (
    ChapterPlan,
    GraderEvaluation,
    Issue,
)
from schemas.knowledge.inputs import UserProfile


logger = logging.getLogger(__name__)


# =============================================================================
# Tuning constants
# =============================================================================
# Weighted composite mirrors the GRADER_PROMPT's "double weight on
# signal_to_noise + citation_integrity + code_preservation_ratio" guidance.
# Tweakable here; the LLM grader uses the same logical weighting in prose
# but the values were never numerically specified — this is the first time
# they're explicit.
_DIM_WEIGHTS: dict[str, float] = {
    "signal_to_noise":          2.0,  # double-weighted per GRADER_PROMPT
    "citation_integrity":       2.0,  # double-weighted per GRADER_PROMPT
    "code_preservation_ratio":  2.0,  # double-weighted per GRADER_PROMPT
    "code_density":             1.0,
    "assumption_match":         1.0,
    "job_alignment":            1.0,
    "portfolio_synergy":        1.0,
    "complexity_appropriate":   1.0,
    "market_analysis":          1.0,
}

# Action thresholds — match the GRADER_PROMPT semantics (lines 631-636).
_REFINE_FLOOR = 0.60   # below this → regenerate (structural failure)


# =============================================================================
# Regex patterns (compiled once at module load)
# =============================================================================
# signal_to_noise — boilerplate intro phrases that hurt code-first density.
# Sourced from GRADER_PROMPT calibration anchors + curator's "transition
# language" blacklist in CURATOR_PROMPT lines 824-825. Each match is a
# concrete penalty + a span-anchored Issue.
_BOILERPLATE_LINE_RE = re.compile(
    r"^.*?\b("
    r"in this chapter,? we will"
    r"|let'?s (?:explore|dive into|take a look)"
    r"|by the end of this (?:chapter|section)"
    r"|we will learn"
    r"|in conclusion,?"
    r"|to summarize,?"
    r"|to wrap up,?"
    r"|furthermore,?"
    r"|moreover,?"
    r"|it is important to note"
    r"|note that"
    r"|building on the previous (?:section|chapter)"
    r"|alright,?"
    r"|so,?"
    r")\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

# Stub-marker detection — already part of pre-gates but also penalize via
# signal_to_noise so it shows up in the dim breakdown, not just the gate.
_STUB_RE = re.compile(r"\b(TODO|TBD|PLACEHOLDER|FIXME|XXX)\b(?![^`]*`)", re.MULTILINE)

# Heading shape — "Summary" / "Conclusion" sections are also a code-density
# regression per GRADER_PROMPT and CURATOR_PROMPT structural rules.
_SUMMARY_HEADING_RE = re.compile(
    r"^#+\s*(summary|conclusion|recap|takeaways?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Citation regex — kept consistent with `_deterministic_grader_gates` so the
# two pathways agree (the LLM gate and the classical scorer must produce
# the same count on the same input).
_CITATION_RE = re.compile(r"#\s*docs:\s*[\w/.\-]+")

# Fenced code blocks — same regex as the pre-gate.
_FENCE_RE = re.compile(r"^(?:```|~~~)", re.MULTILINE)


# =============================================================================
# Per-dimension scorers — each returns (score ∈ [0,1], list[Issue])
# =============================================================================
def score_signal_to_noise(
    synthesis_text: str,
) -> tuple[float, list[Issue]]:
    """
    Penalize boilerplate intro phrases, stub markers, and Summary/Conclusion
    sections. Each match becomes an `Issue` with `span_quote` = the matched
    line (clamped 10–200 chars per Issue schema) and a surgical suggestion.

    Score formula:
        violations = (boilerplate lines) + (stub markers) + (summary headings)
        score = max(0, 1 - 0.05 * violations)
    Tunable: 0.05 means 20 violations → 0.0 score. Empirically chapters in
    Run-9 / Run-10 era had 0–5 boilerplate lines, so this maps to scores in
    the 0.75–1.00 band that the LLM grader was already producing for
    well-written chapters.
    """
    issues: list[Issue] = []
    violations = 0

    for m in _BOILERPLATE_LINE_RE.finditer(synthesis_text):
        violations += 1
        span = m.group(0).strip()
        # Clamp span to 10-200 char Issue field bounds
        span_quote = span[:200] if len(span) >= 10 else span.ljust(10)
        issues.append(Issue(
            span_quote=span_quote,
            dimension="signal_to_noise",
            suggestion="Delete this filler line; open with code or substantive prose.",
        ))

    for m in _STUB_RE.finditer(synthesis_text):
        violations += 1
        # Capture surrounding context for the span_quote (40 chars before+after)
        ctx_start = max(0, m.start() - 40)
        ctx_end = min(len(synthesis_text), m.end() + 40)
        span = synthesis_text[ctx_start:ctx_end].strip()
        span_quote = span[:200] if len(span) >= 10 else span.ljust(10)
        issues.append(Issue(
            span_quote=span_quote,
            dimension="signal_to_noise",
            suggestion=f"Replace stub marker '{m.group(0)}' with real content.",
        ))

    for m in _SUMMARY_HEADING_RE.finditer(synthesis_text):
        violations += 1
        span = m.group(0).strip()
        span_quote = span[:200] if len(span) >= 10 else span.ljust(10)
        issues.append(Issue(
            span_quote=span_quote,
            dimension="signal_to_noise",
            suggestion="Delete Summary/Conclusion section; code-first chapters don't recap.",
        ))

    score = max(0.0, 1.0 - 0.05 * violations)
    return score, issues


def score_citation_integrity(
    synthesis_text: str,
    chapter: ChapterPlan,
) -> tuple[float, list[Issue]]:
    """
    Fraction of `# docs:` citations relative to chapter.assigned_files count.
    A chapter that cites every assigned file gets 1.0; one that cites half
    gets 0.5. Cap at 1.0 (over-citing isn't a penalty).

    No `Issue` is produced for partial citation — the LLM grader would
    have to enumerate which files weren't cited, but the deterministic
    scorer can do better: emit one Issue per uncited assigned_file.
    """
    citations = _CITATION_RE.findall(synthesis_text)
    n_citations = len(citations)
    n_assigned = max(1, len(chapter.assigned_files))
    score = min(1.0, n_citations / n_assigned)

    issues: list[Issue] = []
    if score < 1.0:
        # Identify which assigned_files are NOT cited and flag each
        cited_slugs: set[str] = set()
        for c in citations:
            # Extract the slug after "# docs: "
            m = re.search(r"#\s*docs:\s*([\w/.\-]+)", c)
            if m:
                cited_slugs.add(m.group(1).strip().lower())
        for slug in chapter.assigned_files[:5]:  # cap at 5 issues to avoid noise
            if slug.lower() not in cited_slugs:
                issues.append(Issue(
                    span_quote=f"(missing citation for {slug})".ljust(10),
                    dimension="citation_integrity",
                    suggestion=f"Add `# docs: {slug}` next to the claim drawn from this source.",
                ))

    return score, issues


def score_code_density(
    synthesis_text: str,
) -> tuple[float, list[Issue]]:
    """
    Fraction of non-blank lines that are inside fenced code blocks.
    Target: 0.4 (junior) / 0.55 (mid) / 0.7 (senior). The score itself is
    just the raw ratio; the user-profile-aware target lives in
    `complexity_appropriate`. This dim stays profile-agnostic.

    Identical computation to `_deterministic_grader_gates` lines 2110-2119
    (kept consistent so pre-gate and full scorer agree).
    """
    non_blank = sum(1 for line in synthesis_text.split("\n") if line.strip())
    if non_blank == 0:
        return 0.0, [Issue(
            span_quote="(empty chapter)".ljust(10),
            dimension="code_density",
            suggestion="Chapter has no non-blank lines — regenerate.",
        )]

    in_fence = False
    code_lines = 0
    for line in synthesis_text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence and line.strip():
            code_lines += 1

    score = code_lines / non_blank
    issues: list[Issue] = []
    # Hard floor — every chapter should be at least 25% code. Below that
    # the synth lost the code-first invariant.
    if score < 0.25:
        issues.append(Issue(
            span_quote=synthesis_text[:200].strip().ljust(10),
            dimension="code_density",
            suggestion=f"Code/prose ratio {score:.0%} below 25% floor — synth lost the code-first invariant.",
        ))
    return score, issues


def score_job_alignment(
    synthesis_text: str,
    user_profile: UserProfile,
) -> tuple[float, list[Issue]]:
    """
    Substring match on user_profile.target_markets keywords. A chapter
    that explicitly references a target market (e.g., "UAE", "G42", "DBS",
    "Singapore", "Stargate") gets credit. Empty target_markets → returns
    1.0 (no penalty when user didn't declare markets).
    """
    if not user_profile.target_markets:
        return 1.0, []

    text_lower = synthesis_text.lower()
    hits = 0
    for market in user_profile.target_markets:
        if market.lower() in text_lower:
            hits += 1
    score = min(1.0, hits / len(user_profile.target_markets))

    issues: list[Issue] = []
    if score < 0.5:
        missing = [m for m in user_profile.target_markets if m.lower() not in text_lower]
        issues.append(Issue(
            span_quote=(synthesis_text[:200] if synthesis_text else "").strip().ljust(10),
            dimension="job_alignment",
            suggestion=f"Weave in references to: {', '.join(missing[:3])}.",
        ))
    return score, issues


def score_portfolio_synergy(
    synthesis_text: str,
    user_profile: UserProfile,
) -> tuple[float, list[Issue]]:
    """
    Substring match on user_profile.portfolio_refs. Same shape as
    job_alignment but on the user's existing project list.
    """
    if not user_profile.portfolio_refs:
        return 1.0, []

    text_lower = synthesis_text.lower()
    hits = 0
    for ref in user_profile.portfolio_refs:
        if ref.lower() in text_lower:
            hits += 1
    score = min(1.0, hits / len(user_profile.portfolio_refs))

    issues: list[Issue] = []
    if score < 0.3:
        missing = [r for r in user_profile.portfolio_refs[:3] if r.lower() not in text_lower]
        if missing:
            issues.append(Issue(
                span_quote=(synthesis_text[:200] if synthesis_text else "").strip().ljust(10),
                dimension="portfolio_synergy",
                suggestion=f"Cross-reference portfolio projects: {', '.join(missing)}.",
            ))
    return score, issues


def score_code_preservation_ratio(
    audit_summary: str,
) -> float:
    """
    Passthrough from the upstream deterministic audit. The `audit_summary`
    string is the same one that gets fed to the LLM grader; we parse it
    for the "preservation=X.XX" value the audit pipeline already emits.

    Defaults to 1.0 if the audit didn't run (e.g., prose-only chapter or
    audit-bypassed path).
    """
    m = re.search(r"preservation\s*=\s*([0-9]*\.?[0-9]+)", audit_summary or "")
    if m:
        try:
            v = float(m.group(1))
            return max(0.0, min(1.0, v))
        except ValueError:
            return 1.0
    return 1.0


# =============================================================================
# Phase 1.2 (2026-05-13) — real implementations replacing the stubs
# =============================================================================
# Regex pattern for assumption_match — matches "definition templates" of
# mastered technologies. Built dynamically per-call against
# user_profile.mastered_technologies. Catches sentences like:
#   "Python is a programming language..."
#   "FastAPI is a web framework..."
#   "A Kubernetes pod is..."
# Each match counts as a violation (chapter is re-explaining what the
# reader is presumed to know).
def _build_assumption_pattern(tech: str) -> re.Pattern:
    """Compile a regex that catches definitional sentences about `tech`."""
    return re.compile(
        r"(?:^|\.\s+|^\s*)"
        r"(?:a\s+|an\s+|the\s+)?"
        + re.escape(tech)
        + r"\s+(?:is|are)\s+(?:a|an|the)?\s*\w+",
        re.IGNORECASE | re.MULTILINE,
    )


def score_assumption_match(
    synthesis_text: str,
    user_profile: UserProfile,
) -> tuple[float, list[Issue]]:
    """
    Penalize sentences that re-define technologies the user already
    knows. Regex heuristic — chosen over NLI (ModernBERT-MNLI was the
    Phase 1.2 candidate) to respect the no-in-cluster-inference rule
    per `feedback_local_vs_rotator_architecture` memory. NLI host-side
    via llama-server stays available if accuracy is insufficient.

    Score formula:
        violations = total definitional matches across mastered_techs
        score = max(0, 1 - 0.1 * violations)
    Empty mastered_technologies → 1.0 (no penalty when undeclared).
    """
    if not user_profile.mastered_technologies:
        return 1.0, []

    issues: list[Issue] = []
    violations = 0
    for tech in user_profile.mastered_technologies:
        pattern = _build_assumption_pattern(tech)
        for m in pattern.finditer(synthesis_text):
            violations += 1
            if len(issues) < 6:  # cap issues per chapter to avoid noise
                ctx_start = max(0, m.start() - 20)
                ctx_end = min(len(synthesis_text), m.end() + 40)
                span = synthesis_text[ctx_start:ctx_end].strip()
                span_quote = span[:200] if len(span) >= 10 else span.ljust(10)
                issues.append(Issue(
                    span_quote=span_quote,
                    dimension="assumption_match",
                    suggestion=f"Reader knows {tech!r}; drop this definitional sentence.",
                ))

    score = max(0.0, 1.0 - 0.1 * violations)
    return score, issues


# textstat lazy-imported in score_complexity_appropriate so the module
# loads even if textstat isn't installed (e.g., during early Phase 1.1
# A/B testing). Failures fall back to 1.0 → no penalty.
def score_complexity_appropriate(
    synthesis_text: str,
    user_profile: UserProfile,
) -> tuple[float, list[Issue]]:
    """
    Flesch-Kincaid grade-level scoring targeted to user_profile.level.
    Target bands (grade level):
        junior: 8-11   (mid-school to early high school)
        mid:    12-14  (high school graduate, early college)
        senior: 14-17  (college and above)
    Score 1.0 inside the band, exp-decay outside. Empty text → 0.0.
    """
    text = (synthesis_text or "").strip()
    if not text or len(text) < 80:
        return 0.0, [Issue(
            span_quote="(text too short to score)".ljust(10),
            dimension="complexity_appropriate",
            suggestion=f"Chapter has only {len(text)} chars — too short for grade-level scoring.",
        )]

    try:
        import textstat as _ts
        measured = float(_ts.flesch_kincaid_grade(text))
    except Exception as e:
        logger.warning(
            f"[grader-classical] textstat unavailable or errored "
            f"({type(e).__name__}: {e}); skipping complexity_appropriate"
        )
        return 1.0, []

    target_band = {
        "junior": (8.0, 11.0),
        "mid":    (12.0, 14.0),
        "senior": (14.0, 17.0),
    }.get(user_profile.level, (10.0, 14.0))

    if target_band[0] <= measured <= target_band[1]:
        return 1.0, []

    diff = min(abs(measured - target_band[0]), abs(measured - target_band[1]))
    # 5-grade deviation → score 0; tunable.
    score = max(0.0, 1.0 - diff / 5.0)
    issues: list[Issue] = []
    if score < 0.7:
        direction = "simpler" if measured > target_band[1] else "more sophisticated"
        issues.append(Issue(
            span_quote=text[:200].ljust(10),
            dimension="complexity_appropriate",
            suggestion=(
                f"Flesch-Kincaid grade {measured:.1f} outside target "
                f"{target_band[0]:.0f}-{target_band[1]:.0f} for "
                f"{user_profile.level}; rewrite {direction}."
            ),
        ))
    return score, issues


# Pydantic schema for the market_analysis small-LLM call.
# Defined module-level so LiteLLM Router can cache the JSON schema once.
from pydantic import BaseModel as _BaseModel, Field as _Field


class _MarketAnalysisJudgment(_BaseModel):
    """LLM output schema for market_analysis grader dim."""
    score: float = _Field(ge=0.0, le=1.0,
                          description="0-1 market alignment score")
    reasoning: str = _Field(max_length=200,
                            description="One-sentence rationale, ≤200 chars")


async def score_market_analysis(
    synthesis_text: str,
    user_profile: UserProfile,
    framework: str,
) -> tuple[float, list[Issue]]:
    """
    Small-LLM judgment on whether the chapter's content aligns with the
    user's target_markets (e.g., "UAE", "Singapore", "Brazil"). Uses the
    `kd-reduce-label` rotator group (validated 2026-05-11 — same pool
    used for REDUCE meta-labeling). ~500 token prompt, ~50 token output,
    json_schema-enforced structure.

    No target_markets declared → returns 1.0 (no penalty).
    LLM call failure → returns 0.5 (neutral; logged as warning).
    """
    if not user_profile.target_markets:
        return 1.0, []

    text = (synthesis_text or "").strip()
    if len(text) < 80:
        return 0.0, [Issue(
            span_quote="(text too short)".ljust(10),
            dimension="market_analysis",
            suggestion=f"Chapter too short ({len(text)} chars) to evaluate.",
        )]

    try:
        from services.llm_chain import build_reduce_label_chain
    except Exception as e:
        logger.warning(
            f"[grader-classical] cannot import build_reduce_label_chain "
            f"({type(e).__name__}: {e}); defaulting market_analysis to 0.5"
        )
        return 0.5, []

    excerpt = text[:1500]
    markets = ", ".join(user_profile.target_markets)
    prompt_text = (
        f"Score on a 0-1 scale whether this {framework} study chapter contains "
        f"content that is monetizable or directly relevant to the target "
        f"markets: [{markets}].\n"
        f"1.0 = strong, concrete, actionable references (specific companies, "
        f"compliance regimes, or buyer profiles in the target markets).\n"
        f"0.5 = generic technical content with no market-specific hooks.\n"
        f"0.0 = no relevant content at all.\n\n"
        f"Chapter excerpt (first 1500 chars):\n{excerpt}\n\n"
        f"Output JSON with `score` (float 0-1) and `reasoning` (≤200 chars)."
    )

    try:
        llm = build_reduce_label_chain()
        chain = llm.with_structured_output(
            _MarketAnalysisJudgment, method="json_schema",
        )
        result = await chain.ainvoke(prompt_text)
        score = float(result.score)
        score = max(0.0, min(1.0, score))  # clamp defensively
        issues: list[Issue] = []
        if score < 0.5:
            reasoning = (result.reasoning or "")[:80]
            issues.append(Issue(
                span_quote=text[:200].ljust(10),
                dimension="market_analysis",
                suggestion=f"Strengthen {markets} hooks: {reasoning}",
            ))
        return score, issues
    except Exception as e:
        logger.warning(
            f"[grader-classical] market_analysis LLM call failed "
            f"({type(e).__name__}: {str(e)[:120]}); defaulting to 0.5"
        )
        return 0.5, []


# =============================================================================
# Top-level scorer — drop-in replacement for the LLM grader
# =============================================================================
async def score_chapter_classically(
    synthesis_text: str,
    chapter: ChapterPlan,
    user_profile: UserProfile,
    audit_summary: str = "",
    framework: str = "generic",
) -> GraderEvaluation:
    """
    Compute all 9 grader dimensions classically (Phase 1.2: 8 deterministic
    + 1 small-LLM for market_analysis) and assemble a `GraderEvaluation`
    matching the LLM grader's output shape.

    Async because `score_market_analysis` makes one small LLM call via the
    `kd-reduce-label` rotator. All other scorers are sync; we run the 8
    deterministic scorers first, then await the single LLM call.

    The composite `weighted_score` is the weighted average using
    `_DIM_WEIGHTS`. Action follows the same rule as `GRADER_PROMPT`:
        composite >= user_profile.acceptance_threshold → "accept"
        composite < threshold but >= _REFINE_FLOOR     → "refine"
        composite < _REFINE_FLOOR                      → "regenerate"

    Issues are collected from each scorer that produced one. Each
    Issue is span-anchored (verbatim quote from the chapter or a
    descriptive marker) — same shape the refiner expects.
    """
    # 8 deterministic scorers — pure-Python, sub-millisecond
    s_signal, i_signal = score_signal_to_noise(synthesis_text)
    s_cite, i_cite = score_citation_integrity(synthesis_text, chapter)
    s_code_density, i_code_density = score_code_density(synthesis_text)
    s_job, i_job = score_job_alignment(synthesis_text, user_profile)
    s_portfolio, i_portfolio = score_portfolio_synergy(synthesis_text, user_profile)
    s_assumption, i_assumption = score_assumption_match(synthesis_text, user_profile)
    s_complexity, i_complexity = score_complexity_appropriate(synthesis_text, user_profile)
    s_preservation = score_code_preservation_ratio(audit_summary)

    # 1 small-LLM call via kd-reduce-label rotator (Phase 1.2)
    s_market, i_market = await score_market_analysis(
        synthesis_text, user_profile, framework,
    )

    dim_scores = {
        "signal_to_noise":          s_signal,
        "citation_integrity":       s_cite,
        "code_density":             s_code_density,
        "job_alignment":            s_job,
        "portfolio_synergy":        s_portfolio,
        "assumption_match":         s_assumption,
        "complexity_appropriate":   s_complexity,
        "code_preservation_ratio":  s_preservation,
        "market_analysis":          s_market,
    }

    # Weighted composite
    total_weight = sum(_DIM_WEIGHTS[d] for d in dim_scores)
    weighted_score = sum(
        dim_scores[d] * _DIM_WEIGHTS[d] for d in dim_scores
    ) / total_weight if total_weight > 0 else 0.0

    # Action
    threshold = float(user_profile.acceptance_threshold)
    if weighted_score >= threshold:
        action: str = "accept"
    elif weighted_score >= _REFINE_FLOOR:
        action = "refine"
    else:
        action = "regenerate"

    # Aggregate all issues, dedupe by (dimension, span_quote)
    all_issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for batch in (i_signal, i_cite, i_code_density, i_job, i_portfolio,
                  i_assumption, i_complexity, i_market):
        for issue in batch:
            key = (issue.dimension, issue.span_quote[:60])
            if key not in seen:
                all_issues.append(issue)
                seen.add(key)
    # Cap at 10 issues per GRADER_PROMPT line 691
    all_issues = all_issues[:10]

    return GraderEvaluation(
        signal_to_noise=s_signal,
        assumption_match=s_assumption,
        job_alignment=s_job,
        citation_integrity=s_cite,
        code_density=s_code_density,
        portfolio_synergy=s_portfolio,
        complexity_appropriate=s_complexity,
        market_analysis=s_market,
        code_preservation_ratio=s_preservation,
        weighted_score=weighted_score,
        specific_issues=all_issues,
        action=action,
    )
