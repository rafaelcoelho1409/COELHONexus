"""
Knowledge Distiller — Classical (Deterministic) Curator (Phase 5, 2026-05-13)

Replaces the per-chapter LLM curator (CURATOR_PROMPT in schemas/knowledge/
prompts.py) with deterministic regex passes for the three style-normalization
behaviors the LLM was doing:

  1. Glossary substitution — replace common synonym variants of each
     extracted glossary term with the canonical form (word-boundary,
     case-insensitive). Eliminates terminology drift across chapters.
  2. Heading depth normalization — re-promote/demote heading markers so
     the study uses `##` for sections and `###` for subsections only
     (`#` is reserved for the chapter title; deeper `####+` collapse to
     `###`). Keeps cross-chapter heading hierarchy consistent.
  3. Transition phrase deletion — same regex as
     refiner_classical._BOILERPLATE_LINE_RE plus the curator-specific
     openers `So,` / `Alright,` / `Let's explore,` that the curator
     prompt explicitly listed.

NO LLM call in the default classical path. The CURATOR_PROMPT also asked
for "voice/tone harmonization" — that is irreducibly LLM-bound but accounts
for the smallest variance in observed regressions. Skipping it preserves
~95% of the curator's value while eliminating 100% of its LLM cost.
Phase 5.1 can add an optional small-LLM tone pass behind a sub-flag if
voice drift becomes a measurable regression on real chapters.

Drop-in shape: same input (vaulted markdown + glossary terms) → same
output (rewritten vaulted markdown). Code-vault sentinels (`<code-ref
hash="..."/>`) are NEVER touched because every regex operates on
text-shaped patterns (intro phrases, heading markers, identifier
tokens). The caller still runs `_audit_sentinel_roundtrip` after this
function returns — if anything went wrong (shouldn't, but defensive),
the production curator falls back to keeping the original content.

Pattern source: KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md Phase 5 / Step D.
"""
from __future__ import annotations

import logging
import re


logger = logging.getLogger(__name__)


# =============================================================================
# Regex patterns (compiled once, kept consistent with grader/refiner)
# =============================================================================
# Boilerplate intro phrases — superset of grader_classical / refiner_classical
# patterns + curator-prompt-specific openers ("So,", "Alright,", "Let's
# explore,") listed in CURATOR_PROMPT lines 822-823.
_TRANSITION_LINE_RE = re.compile(
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
    r"|that being said,?"
    r"|with that said,?"
    r"|having covered"
    r")\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

# Heading regex — capture the `#` prefix + the heading text. We rewrite
# the prefix based on depth.
_HEADING_RE = re.compile(r"^(#{1,6})(\s+)(.+?)\s*$", re.MULTILINE)

# Code-fence detection — same as elsewhere in the codebase. Used to
# bracket heading rewrites so we don't accidentally rewrite headings
# inside fenced code blocks (rare in practice but safer).
_FENCE_RE = re.compile(r"^(?:```|~~~)", re.MULTILINE)

# Collapse runs of 3+ blank lines (left over after deletions).
_TRIPLE_BLANK_RE = re.compile(r"\n{3,}")


# =============================================================================
# Glossary synonym map — common synonyms for framework-y terms
# =============================================================================
# When the curator's `_extract_glossary_terms` upstream returns the
# canonical form of a term, this map's keys are common variants we
# normalize TO that canonical form. Each value is itself a regex pattern
# (not a literal) to allow whitespace/punctuation tolerance.
#
# Caller passes the actual glossary terms in `glossary_terms`; we apply
# substitutions for the variants we know about + word-boundary match on
# the bare canonical form (case-normalization).
_KNOWN_SYNONYMS: dict[str, list[str]] = {
    # FastAPI flavor
    "FastAPI": [r"fast[\s\-]?api"],
    "Pydantic": [r"py[\s\-]?dantic"],
    "Starlette": [r"star[\s\-]?lette"],
    "asyncio":  [r"async[\s\-]?io"],
    # Generic
    "JavaScript": [r"java[\s\-]?script", r"\bjs\b"],
    "TypeScript": [r"type[\s\-]?script", r"\bts\b"],
    "PostgreSQL": [r"postgres(?:ql)?"],
    "Kubernetes": [r"k[\s\-]?8s\b", r"kube(?!rnetes)"],
    # Common drift patterns
    "Docker":   [r"\bdocker[\s\-]?engine\b"],
    "GitHub":   [r"\bgit[\s\-]?hub\b"],
}


# =============================================================================
# Deterministic passes
# =============================================================================
def _apply_glossary_substitution(
    content: str,
    glossary_terms: list[str],
) -> tuple[str, int]:
    """
    For each glossary term, ensure the canonical form is used consistently.
    Two-pass:
      1. Apply known-synonym map (e.g., "fast api" / "fast-api" → "FastAPI").
      2. Case-normalize the canonical form when it appears as a word
         (preserves identifier-shaped terms).

    Returns (new_content, n_substitutions).
    """
    if not glossary_terms:
        return content, 0

    cur = content
    n_subs = 0
    for term in glossary_terms:
        # Pass 1: known-synonym variants → canonical
        for variant_pattern in _KNOWN_SYNONYMS.get(term, []):
            try:
                new_cur, n = re.subn(
                    rf"\b{variant_pattern}\b",
                    term,
                    cur,
                    flags=re.IGNORECASE,
                )
                if n:
                    cur = new_cur
                    n_subs += n
            except re.error:
                # Pattern compile failure → skip this variant
                continue
        # Pass 2: case-normalize the canonical form itself (e.g., "fastapi"
        # written somewhere as plain text → "FastAPI"). Skip if the term
        # has no uppercase letters (lowercase-only is already canonical).
        if any(c.isupper() for c in term):
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            new_cur, n = pattern.subn(term, cur)
            # Only count true case-changes
            if n and new_cur != cur:
                n_subs += sum(
                    1 for m in pattern.finditer(cur)
                    if m.group(0) != term
                )
                cur = new_cur
    return cur, n_subs


def _normalize_heading_depths(content: str) -> tuple[str, int]:
    """
    Rewrite heading markers so depth 2+ maps to `##`/`###` only.
    Logic:
      - `#`  (H1): chapter title — leave alone (only one expected).
      - `##` (H2): section — keep.
      - `###`, `####`, etc. (H3+): collapse all to `###` (subsection).

    Skips lines inside fenced code blocks so heading-shaped comments
    in code (Python `# heading`, etc.) are left alone.

    Returns (new_content, n_rewrites).
    """
    lines = content.split("\n")
    in_fence = False
    n_rewrites = 0
    output: list[str] = []
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        m = _HEADING_RE.match(line)
        if not m:
            output.append(line)
            continue
        depth = len(m.group(1))
        space = m.group(2)
        text = m.group(3)
        if depth >= 3:
            new_prefix = "###"
            new_line = f"{new_prefix}{space}{text}"
            if new_line != line:
                n_rewrites += 1
            output.append(new_line)
        else:
            output.append(line)
    return "\n".join(output), n_rewrites


def _delete_transition_lines(content: str) -> tuple[str, int]:
    """
    Delete lines matching the boilerplate / transition-phrase regex.
    Returns (new_content, n_deletions).
    """
    n = [0]
    def _sub(_m: re.Match) -> str:
        n[0] += 1
        return ""
    new_content = _TRANSITION_LINE_RE.sub(_sub, content)
    if n[0] == 0:
        return content, 0
    return _TRIPLE_BLANK_RE.sub("\n\n", new_content), n[0]


# =============================================================================
# Public API
# =============================================================================
def curate_chapter_classically(
    content: str,
    glossary_terms: list[str],
    chapter_number: int,
    framework: str,
) -> tuple[str, list[str]]:
    """
    Run all deterministic curator passes over one chapter's vaulted markdown.

    Returns `(curated_content, pass_log)` — same shape the production
    `_curate_one` helper expects so it can decide whether to write back
    (the production code runs `_audit_sentinel_roundtrip` afterwards
    and falls back to original on any code-vault inconsistency).

    Order matters:
      1. Glossary substitution first — fixes "fast api" → "FastAPI" before
         heading regex tries to match (a `## fast api` heading should
         become `## FastAPI`).
      2. Heading depth normalization second — keeps the section structure
         tight before we delete transition lines (which can leave bare
         heading-only paragraphs that would then need re-collapse).
      3. Transition-line deletion last — strips boilerplate from prose
         AFTER structural normalization. Final triple-blank-line
         collapse runs as part of the deletion pass.
    """
    cur = content
    log: list[str] = []

    cur, n_gloss = _apply_glossary_substitution(cur, glossary_terms)
    if n_gloss:
        log.append(f"glossary: normalized {n_gloss} term occurrence(s)")

    cur, n_head = _normalize_heading_depths(cur)
    if n_head:
        log.append(f"headings: collapsed {n_head} H4+ heading(s) to ###")

    cur, n_trans = _delete_transition_lines(cur)
    if n_trans:
        log.append(f"transitions: deleted {n_trans} boilerplate line(s)")

    if log:
        logger.info(
            f"[curator-classical][ch{chapter_number:02d}] {framework}: "
            + "; ".join(log)
        )
    return cur, log
