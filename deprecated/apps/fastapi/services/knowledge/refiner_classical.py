"""
Knowledge Distiller — Classical (Deterministic) Refiner (Phase 4, 2026-05-13)

Replaces the LLM-based Self-Refine adjustment generator (ADJUSTMENT_PROMPT in
schemas/knowledge/prompts.py) with deterministic per-dimension patch handlers
plus classical adjustment-text templates. Drop-in shape for the existing
Self-Refine loop in graphs/knowledge/distiller.py.

Two layers of savings (vs the legacy 2 LLM calls/iter — ADJUSTMENT + re-synth):

  Layer 1 — `apply_classical_patches`:
    For PATCHABLE dimensions, edit the chapter markdown directly. If all
    grader issues resolve via patches alone, the caller re-grades the
    patched content; if that re-grade accepts, the next re-synth iter is
    SKIPPED entirely (saves the full 2 LLM calls).

  Layer 2 — `generate_adjustment_classically`:
    When residual non-patchable issues remain (or patches couldn't apply),
    emit the adjustment text deterministically from per-dim templates.
    Replaces the small `_generate_adjustment` LLM call (saves 1 LLM call).

PATCHABLE dimensions (regex edits can fix in place):
  - signal_to_noise       — delete boilerplate lines, stub markers, Summary/
                            Conclusion sections
  - citation_integrity    — append `# docs: <slug>` for each missing assigned_file
                            (slug carried in the Issue's suggestion text)
  - assumption_match      — delete definitional sentences for mastered_techs
                            (`{Tech} is a {definition}`-style templates)

NON-PATCHABLE dimensions (need real rewrite — go to residual):
  - code_density, job_alignment, portfolio_synergy,
    complexity_appropriate, market_analysis
  - code_preservation_ratio is deterministic upstream and never an Issue dim

Pattern source: KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md Phase 4 / Step C-refine.
Dependency: Phase 1 (classical grader) — Issue.dimension labels are reliable.
"""
from __future__ import annotations

import logging
import re

from schemas.knowledge.agents import (
    ChapterPlan,
    GraderEvaluation,
    Issue,
)
from schemas.knowledge.inputs import UserProfile


logger = logging.getLogger(__name__)


# =============================================================================
# Patchable dimension set
# =============================================================================
_PATCHABLE_DIMS: frozenset[str] = frozenset({
    "signal_to_noise",
    "citation_integrity",
    "assumption_match",
})


# =============================================================================
# Regex patterns (compiled once; kept consistent with grader_classical)
# =============================================================================
# Boilerplate intro phrases — same set as grader_classical._BOILERPLATE_LINE_RE.
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

# Stub markers — outside inline code spans. The negative lookahead is
# line-scoped (`[^`\n]*`) so a TODO on a prose line isn't masked by a
# backtick that appears further down the document.
_STUB_LINE_RE = re.compile(
    r"^.*\b(TODO|TBD|PLACEHOLDER|FIXME|XXX)\b(?![^`\n]*`).*$",
    re.MULTILINE,
)

# Summary/Conclusion headings — `^#+ Summary` etc.
_SUMMARY_HEADING_RE = re.compile(
    r"^(#+)\s*(summary|conclusion|recap|takeaways?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Generic markdown heading — used to bound section deletion.
_SECTION_HEAD_RE = re.compile(r"^(#{1,6})\s+.+?$", re.MULTILINE)

# Extract slug from a grader suggestion like:
#   "Add `# docs: quickstart.md` next to the claim drawn from this source."
_SLUG_FROM_SUGGESTION_RE = re.compile(r"#\s*docs:\s*([\w/.\-]+)")

# Collapse runs of 3+ blank lines (left over after deletions) down to 2.
_TRIPLE_BLANK_RE = re.compile(r"\n{3,}")


# =============================================================================
# Layer 1 helpers — global patches (idempotent, applied once per dim)
# =============================================================================
def _delete_summary_sections(text: str) -> tuple[str, int]:
    """
    Delete every `## Summary` / `## Conclusion` / etc. section: heading line +
    body until the next heading of equal-or-shallower depth (or EOF).
    Returns (new_text, sections_deleted).
    """
    lines = text.split("\n")
    output: list[str] = []
    i = 0
    deleted = 0
    while i < len(lines):
        line = lines[i]
        m = _SUMMARY_HEADING_RE.match(line)
        if m:
            head_depth = len(m.group(1))
            i += 1
            while i < len(lines):
                next_m = _SECTION_HEAD_RE.match(lines[i])
                if next_m and len(next_m.group(1)) <= head_depth:
                    break
                i += 1
            deleted += 1
            continue
        output.append(line)
        i += 1
    if deleted == 0:
        return text, 0
    new_text = _TRIPLE_BLANK_RE.sub("\n\n", "\n".join(output))
    return new_text, deleted


def _delete_boilerplate_lines(text: str) -> tuple[str, int]:
    """Strip lines matching the boilerplate intro-phrase regex."""
    n = [0]
    def _sub(_m: re.Match) -> str:
        n[0] += 1
        return ""
    new_text = _BOILERPLATE_LINE_RE.sub(_sub, text)
    if n[0] == 0:
        return text, 0
    return _TRIPLE_BLANK_RE.sub("\n\n", new_text), n[0]


def _delete_stub_lines(text: str) -> tuple[str, int]:
    """Strip lines containing bare TODO/TBD/PLACEHOLDER/FIXME/XXX markers."""
    n = [0]
    def _sub(_m: re.Match) -> str:
        n[0] += 1
        return ""
    new_text = _STUB_LINE_RE.sub(_sub, text)
    if n[0] == 0:
        return text, 0
    return _TRIPLE_BLANK_RE.sub("\n\n", new_text), n[0]


def _apply_signal_to_noise_patches(text: str) -> tuple[str, list[str]]:
    """
    Apply all signal_to_noise patches globally. Returns (new_text, log).
    Order matters: delete Summary sections first (they may contain boilerplate
    that the boilerplate-pass would otherwise score doubly).
    """
    log: list[str] = []
    cur = text

    cur, n_sec = _delete_summary_sections(cur)
    if n_sec:
        log.append(f"signal_to_noise: deleted {n_sec} Summary/Conclusion section(s)")

    cur, n_bp = _delete_boilerplate_lines(cur)
    if n_bp:
        log.append(f"signal_to_noise: deleted {n_bp} boilerplate intro line(s)")

    cur, n_stub = _delete_stub_lines(cur)
    if n_stub:
        log.append(f"signal_to_noise: deleted {n_stub} stub-marker line(s)")

    return cur, log


def _build_assumption_pattern(tech: str) -> re.Pattern:
    """
    Compile a regex that catches definitional sentences about `tech`.
    Sentence-start anchored: matches at start-of-line (MULTILINE `^`),
    directly after a newline, or after sentence-terminating punctuation
    followed by whitespace. This catches both mid-paragraph and
    paragraph-leading definitional sentences.
    """
    return re.compile(
        r"(?:^|(?<=[.!?]\s)|(?<=\n))"
        r"(?:A\s+|An\s+|The\s+)?"
        + re.escape(tech)
        + r"\s+(?:is|are)\s+[^.!?\n]{0,200}[.!?]",
        re.IGNORECASE | re.MULTILINE,
    )


def _apply_assumption_match_patches(
    text: str,
    user_profile: UserProfile,
) -> tuple[str, list[str]]:
    """
    Delete sentences that re-define technologies the user already knows.
    Iterates per `mastered_technologies` entry. Skips deletions over 400
    chars (defensive — likely matched too greedily).
    """
    log: list[str] = []
    if not user_profile.mastered_technologies:
        return text, log

    cur = text
    for tech in user_profile.mastered_technologies:
        pattern = _build_assumption_pattern(tech)
        matches = list(pattern.finditer(cur))
        if not matches:
            continue
        n_applied = 0
        for m in reversed(matches):
            if m.end() - m.start() > 400:
                continue
            cur = cur[:m.start()] + cur[m.end():]
            n_applied += 1
        if n_applied:
            log.append(
                f"assumption_match: deleted {n_applied} definitional "
                f"sentence(s) about {tech!r}"
            )

    if log:
        cur = _TRIPLE_BLANK_RE.sub("\n\n", cur)
    return cur, log


def _patch_citation_integrity(
    text: str,
    issue: Issue,
) -> tuple[str, str | None]:
    """
    Per-issue citation patch — extract slug from suggestion and append a
    `# docs: <slug>` line to the chapter body. Conservative placement (at
    end) — the curator/next-iter re-synth can integrate properly.
    Returns (new_text, applied_slug_or_None).
    """
    m = _SLUG_FROM_SUGGESTION_RE.search(issue.suggestion or "")
    if not m:
        return text, None
    slug = m.group(1).strip()
    citation_line = f"# docs: {slug}"
    if citation_line in text:
        return text, None
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}{citation_line}\n", slug


# =============================================================================
# Public API — Layer 1: apply patches, return residual issues
# =============================================================================
def apply_classical_patches(
    synthesis_text: str,
    issues: list[Issue],
    chapter: ChapterPlan,
    user_profile: UserProfile,
) -> tuple[str, list[Issue], list[str]]:
    """
    Apply deterministic patches per Issue. Returns
    `(patched_text, residual_issues, patch_log)`.

    Strategy:
    - signal_to_noise: apply ALL patches globally if any signal_to_noise Issue
      is present. The dim's three sub-patterns (boilerplate / stub / Summary)
      are all idempotent and cheap, so blanket-apply rather than per-issue
      dispatching from span_quote.
    - assumption_match: apply ALL definitional-sentence deletions globally
      across user_profile.mastered_technologies.
    - citation_integrity: per-Issue, append the cited slug to the body if not
      already present. Independent across issues.
    - non-patchable dims: pass through as residuals untouched.

    Patches that apply but don't change text (e.g. citation already present)
    fall through as residuals so the LLM gets one more shot.
    """
    issues_by_dim: dict[str, list[Issue]] = {}
    for iss in issues:
        issues_by_dim.setdefault(iss.dimension, []).append(iss)

    current = synthesis_text
    residual: list[Issue] = []
    patch_log: list[str] = []

    # signal_to_noise — global pass
    s2n_issues = issues_by_dim.pop("signal_to_noise", [])
    if s2n_issues:
        current, s2n_log = _apply_signal_to_noise_patches(current)
        if s2n_log:
            patch_log.extend(s2n_log)
        else:
            residual.extend(s2n_issues)

    # assumption_match — global pass keyed on mastered_technologies
    am_issues = issues_by_dim.pop("assumption_match", [])
    if am_issues:
        current, am_log = _apply_assumption_match_patches(current, user_profile)
        if am_log:
            patch_log.extend(am_log)
        else:
            residual.extend(am_issues)

    # citation_integrity — per-issue
    ci_issues = issues_by_dim.pop("citation_integrity", [])
    for iss in ci_issues:
        current, applied_slug = _patch_citation_integrity(current, iss)
        if applied_slug:
            patch_log.append(f"citation_integrity: added `# docs: {applied_slug}`")
        else:
            residual.append(iss)

    # All remaining dims go to residual untouched.
    for _dim, items in issues_by_dim.items():
        residual.extend(items)

    return current, residual, patch_log


# =============================================================================
# Public API — Layer 2: deterministic adjustment text
# =============================================================================
_DIM_INSTRUCTIONS: dict[str, str] = {
    "signal_to_noise": (
        "Delete boilerplate phrasing; open sections with code or substantive "
        "prose, not framing language."
    ),
    "code_density": (
        "Increase code/prose ratio — every section should anchor in a fenced "
        "code block or inline `code` runs. Remove abstract preamble; show, "
        "don't tell."
    ),
    "citation_integrity": (
        "Every non-trivial claim drawn from an assigned file needs a "
        "`# docs: <slug>` citation line directly below it."
    ),
    "assumption_match": (
        "Reader already knows these technologies — don't re-define them. "
        "Reference by name and move on."
    ),
    "job_alignment": (
        "Weave in concrete references to the user's target markets — name "
        "specific companies, compliance regimes, or buyer profiles."
    ),
    "portfolio_synergy": (
        "Cross-reference the user's portfolio projects where applicable."
    ),
    "complexity_appropriate": (
        "Rewrite to match the user's level — adjust technical depth, "
        "vocabulary, and density of jargon to the target grade band."
    ),
    "market_analysis": (
        "Strengthen monetization hooks — name concrete buyer profiles, "
        "pricing models, or revenue projections relevant to target markets."
    ),
    "code_preservation_ratio": (
        "Preserve every vault hash exactly once. Distribute hashes across "
        "sections; no orphans, no duplicates."
    ),
}


def generate_adjustment_classically(
    evaluation: GraderEvaluation,
    residual_issues: list[Issue],
    patch_log: list[str],
) -> str:
    """
    Drop-in deterministic replacement for `_generate_adjustment`. Returns
    free-text adjustment string interpolated into SYNTHESIZER_PROMPT's
    `{previous_adjustments}` slot on the next refine iteration.

    Structure (mirrors the LLM's output shape per ADJUSTMENT_PROMPT):
      1. Acknowledge what classical patches already fixed (so the re-synth
         doesn't accidentally re-introduce them).
      2. Per residual dim, surface the canonical surgical instruction +
         specific span_quotes the grader flagged.
      3. End with the composite-score target so the LLM understands stakes.
    """
    lines: list[str] = []

    if patch_log:
        lines.append("CLASSICAL PATCHES already applied — preserve these fixes verbatim:")
        for entry in patch_log[:10]:
            lines.append(f"  - {entry}")
        lines.append("")

    if residual_issues:
        by_dim: dict[str, list[Issue]] = {}
        for iss in residual_issues:
            by_dim.setdefault(iss.dimension, []).append(iss)

        lines.append("REMAINING ISSUES (require re-synth, not patches):")
        for dim, items in by_dim.items():
            instruction = _DIM_INSTRUCTIONS.get(dim, "Address the items below.")
            lines.append("")
            lines.append(f"[{dim}] {instruction}")
            for it in items[:5]:  # cap 5 per dim to avoid prompt bloat
                suggestion = (it.suggestion or "").strip()[:160]
                if suggestion:
                    lines.append(f"  - {suggestion}")
                quote = (it.span_quote or "").replace("\n", " ").strip()[:100]
                if quote and not quote.startswith("("):
                    lines.append(f"    (near: {quote!r})")
    elif not patch_log:
        lines.append(
            "(no specific issues; tighten code density and citation coverage "
            "broadly)"
        )

    lines.append("")
    lines.append(
        f"Target: weighted_score >= acceptance_threshold "
        f"(current: {evaluation.weighted_score:.2f})"
    )
    return "\n".join(lines)
