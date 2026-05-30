"""sawc service — all function definitions.

v2 cookbook schema (2026-05-24 evening): output is structured as
{heading, intro, subtopics: [{subheading, explanation, code_ref_hash}],
citations}. Each subtopic renders as one H3 + 1-2 sentence prose +
ONE code block. See `sawc/types.py` and
`docs/KD-CODE-FIRST-IMPLEMENTATION-2026-05-24.md`.

Bundle 3 additions (2026-05-25):
  - Ship A: schema reorder (hash → subheading → explanation) — types.py.
  - Ship E: subheading↔code identifier check in validate_section_against_inputs.
  - Ship B: explanation↔code identifier overlap check in same validator.
  Both Ship B/E route through the existing 2-attempt repair loop —
  the writer re-emits offending subtopics until alignment holds.
"""
from __future__ import annotations

import ast
import re

from .constants import (
    _MEMORY_SUMMARY_CHARS_MIN,
    _MEMORY_SUMMARY_CHARS_MAX,
    _MEMORY_TERM_CHARS_MAX,
    _MEMORY_TERMS_MAX,
    _SUBTOPICS_MIN,
    _SUBTOPICS_MAX,
    _EXPLANATION_WORDS_MIN,
    _EXPLANATION_WORDS_MAX,
)
from .types import (
    _LLMSectionDraft,
    Citation,
    MemoryEntry,
    SAWCStats,
    Section,
    Subtopic,
)


# =============================================================================
# Code-body identifier extraction (Ship B + E)
# =============================================================================
# Cheap stopword set — tokens too generic to count as "code-anchored".
_IDENT_STOPWORDS = frozenset({
    "self", "cls", "str", "int", "bool", "list", "dict", "set", "tuple",
    "none", "true", "false", "return", "import", "from", "async", "def",
    "class", "yield", "raise", "with", "for", "while", "else", "elif",
    "try", "except", "finally", "not", "and", "the", "this", "that",
    "data", "key", "val", "value", "result", "item", "items", "args",
    "kwargs", "name", "type", "obj", "object", "func", "function",
    "arg", "params", "ctx", "context", "request", "response", "main",
})


def _ast_identifiers(code: str) -> set[str]:
    """Best-effort identifier extraction for code-doc alignment checks.

    Python-AST path covers ~80% of fastmcp/langchain examples. Falls back
    to a regex word grab for shell/markdown/etc snippets. Stopwords are
    dropped so common scaffolding ("self", "return", "data") doesn't
    inflate overlap scores.
    """
    idents: set[str] = set()
    if not code or not code.strip():
        return idents
    # Python AST
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                idents.add(node.id)
            elif isinstance(node, ast.Attribute):
                idents.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                idents.add(node.name)
                # Decorator names too — they're the BIG semantic anchors.
                for d in node.decorator_list:
                    if isinstance(d, ast.Name):
                        idents.add(d.id)
                    elif isinstance(d, ast.Attribute):
                        idents.add(d.attr)
                    elif isinstance(d, ast.Call):
                        if isinstance(d.func, ast.Name):
                            idents.add(d.func.id)
                        elif isinstance(d.func, ast.Attribute):
                            idents.add(d.func.attr)
            elif isinstance(node, ast.ClassDef):
                idents.add(node.name)
            elif isinstance(node, ast.arg):
                idents.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                idents.add(node.arg)
            elif isinstance(node, ast.alias):
                if node.name:
                    idents.add(node.name.split(".")[-1])
                if node.asname:
                    idents.add(node.asname)
    except SyntaxError:
        # Not valid Python — that's fine, the regex fallback below picks up
        # anything that looks identifier-shaped.
        pass

    # Regex fallback covers PascalCase, snake_case, camelCase tokens that
    # AST may have missed (decorators-as-strings, log messages, etc).
    for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", code):
        idents.add(w)

    # Drop stopwords + ultra-short tokens.
    return {
        i for i in idents
        if len(i) >= 3 and i.lower() not in _IDENT_STOPWORDS
    }


def _prose_tokens(text: str) -> set[str]:
    """Pull identifier-like tokens from prose (inline `code` spans get
    PRIORITY; bare word tokens are the bulk)."""
    if not text:
        return set()
    out: set[str] = set()
    # Inline `code` spans — strip backticks; these are the strongest
    # signal that the LLM intentionally cited an identifier.
    for m in re.findall(r"`([^`]+)`", text):
        for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", m):
            out.add(w)
    # Bare alphanumeric tokens.
    for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", text):
        out.add(w)
    return {w for w in out if w.lower() not in _IDENT_STOPWORDS and len(w) >= 3}


def _first_lines_word_set(code: str, n_lines: int = 3) -> set[str]:
    """Lowercased word tokens from the first N non-blank lines of code —
    used as a softer fallback for the subheading-alignment check. Catches
    subheadings like 'Minimal Tool with Type Hints' matching code whose
    first line says `def list_users(ctx: Context)` (overlap via 'tool',
    'def', etc, after we drop stopwords on both sides)."""
    if not code:
        return set()
    out: set[str] = set()
    n = 0
    for raw in code.splitlines():
        line = raw.strip()
        if not line:
            continue
        for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", line):
            wl = w.lower()
            if wl in _IDENT_STOPWORDS:
                continue
            out.add(wl)
        n += 1
        if n >= n_lines:
            break
    return out


def _identifier_overlap(prose: str, code: str) -> tuple[set[str], set[str]]:
    """Return (overlap_set, code_idents). overlap_set ⊆ code_idents are
    the identifiers the prose actually references. Case-sensitive on
    intersection since most code identifiers ARE case-sensitive
    (`get_access_token` ≠ `Get_Access_Token`)."""
    code_idents = _ast_identifiers(code)
    if not code_idents:
        return set(), set()
    prose_set = _prose_tokens(prose)
    if not prose_set:
        return set(), code_idents
    # Exact match first.
    overlap = prose_set & code_idents
    if overlap:
        return overlap, code_idents
    # Fallback: case-insensitive (catches "FastMCP" prose vs "FastMCP" code
    # already a hit; useful when LLM capitalizes differently like
    # `Decorator` vs `decorator`).
    code_lower = {i.lower(): i for i in code_idents}
    prose_lower = {p.lower() for p in prose_set}
    return (
        {code_lower[p] for p in (prose_lower & code_lower.keys())},
        code_idents,
    )


# =============================================================================
# Deterministic memory extraction (v1: no extra LLM call)
# =============================================================================
def extract_memory_entry(
    section: Section,
    section_contributions: list[dict],
    section_heading: str,
) -> MemoryEntry:
    """Derive a compressed MemoryEntry from a freshly-written section
    plus its digest contributions.

    v1 strategy (deterministic — saves N extra LLM calls per chapter):
      - summary: first paragraph of the section, trimmed to fit
                  _MEMORY_SUMMARY_CHARS_MAX
      - key_terminology: extract from contributions[*].key_facts —
                          take the first N words of each fact that
                          looks like an API/type name (capitalized
                          word or `code` span). Dedupe case-fold.

    The shape mirrors what SurveyGen-I §3.2.2 stores in ℳ ("draft
    content + extracted terminology") but skips the LLM-extract step
    in favor of a digest-driven heuristic. Future: mgsr_replan can
    upgrade to LLM-extract if needed.
    """
    # --- summary: combine section intro + first subtopic explanation ---
    parts: list[str] = []
    if section.intro:
        parts.append(section.intro.strip())
    if section.subtopics:
        parts.append(section.subtopics[0].explanation.strip())
    summary = " ".join(parts).strip()
    if len(summary) > _MEMORY_SUMMARY_CHARS_MAX:
        summary = summary[: _MEMORY_SUMMARY_CHARS_MAX - 1].rsplit(" ", 1)[0] + "…"
    if len(summary) < _MEMORY_SUMMARY_CHARS_MIN:
        # Pad with the heading + a generic phrase so the Pydantic min
        # passes; mgsr_replan will flag thin sections via checklist_eval
        summary = (
            f"{section_heading}: {summary}"
            if summary
            else f"{section_heading}: (no content)"
        )
        if len(summary) < _MEMORY_SUMMARY_CHARS_MIN:
            summary = summary + " — content pending refinement."

    # --- terminology: extract code-ish identifiers from key_facts ---
    candidates: list[str] = []
    for contrib in section_contributions or []:
        for fact in (contrib.get("key_facts") or []):
            # Pull `inline_code` spans
            for m in re.finditer(r"`([^`]+)`", fact):
                t = m.group(1).strip()
                if 2 <= len(t) <= _MEMORY_TERM_CHARS_MAX:
                    candidates.append(t)
            # Pull capitalized identifiers (PascalCase or camelCase)
            for m in re.finditer(r"\b([A-Z][a-zA-Z0-9_]{2,})\b", fact):
                t = m.group(1).strip()
                if 3 <= len(t) <= _MEMORY_TERM_CHARS_MAX:
                    candidates.append(t)

    # dedupe case-fold-aware
    seen: set[str] = set()
    terminology: list[str] = []
    for t in candidates:
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        terminology.append(t)
        if len(terminology) >= _MEMORY_TERMS_MAX:
            break

    return MemoryEntry(
        section_id=section.section_id,
        heading=section_heading,
        summary=summary,
        key_terminology=terminology,
    )


# =============================================================================
# Cross-reference validators (post-Pydantic, fail-soft for repair loop)
# =============================================================================
# S3 (2026-05-26 late evening) — hard vs soft issue classification.
#
# The repair loop in sawc/node.py burns budget on EVERY non-empty issue
# list, even when the only issues are quality nudges the LLM can't
# reliably close (subheading↔code identifier overlap, subtopic-count
# shy of bank size). Run 3 evidence: 49+ subheading↔code mismatches and
# 55+ subtopic-shy issues fired across 4 chapters, driving repair rates
# 50-63% with no actual recovery — the LLM either re-picks the wrong
# hash or shrugs.
#
# Issues prefixed with these strings are SOFT — they get reported via
# the section's `.issues` field (so mgsr_replan + checklist see them)
# but don't trigger the writer's repair loop. HARD issues (heading
# drift, hallucinated hash, hallucinated source_key) still drive the
# repair loop because those are correctness failures, not quality
# nudges.
_SOFT_ISSUE_PREFIXES = (
    "subheading↔code mismatch",
    "explanation↔code mismatch",
    "subtopics has only ",
)


def hard_issues(issues: list[str]) -> list[str]:
    """Filter to issues that should trigger the writer repair loop.
    Soft quality-nudge issues are excluded — they still ship for
    downstream visibility via the section's `.issues` field, but the
    writer can't reliably close them in a repair cycle."""
    return [
        i for i in issues
        if not any(i.startswith(p) for p in _SOFT_ISSUE_PREFIXES)
    ]


def validate_section_against_inputs(
    draft: _LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    vault_rich: dict | None = None,
) -> list[str]:
    """Cross-reference rules beyond per-field Pydantic format.

    Returns natural-language issue strings suitable for repair-prompt
    feedback. Empty list = clean.

    Catches:
      - heading drift (LLM didn't echo the outline heading verbatim)
      - hallucinated code_ref hashes (not in allowed_hashes)
      - hallucinated citation source_keys (not in valid_source_keys)
      - Ship E: subheading↔code identifier mismatch (subheading
        describes a different API than the chosen code shows)
      - Ship B: explanation↔code identifier mismatch (prose mentions
        zero identifiers from the chosen code block)

    `vault_rich`: optional dict[hash → VaultEntry-like dict] giving the
    validator access to the actual code bodies. When None, Ship B + E
    checks are skipped (graceful degradation — older callers still work).
    """
    issues: list[str] = []

    if draft.heading.strip().casefold() != expected_heading.strip().casefold():
        issues.append(
            f"heading {draft.heading!r} doesn't match the outline heading "
            f"{expected_heading!r}. Echo the outline heading verbatim."
        )

    # v2 cookbook schema: validate subtopics' code_ref_hash field.
    # Empty hashes are PROSE subtopics (no-code section) — exempt.
    bad_hashes = [
        s.code_ref_hash for s in draft.subtopics
        if s.code_ref_hash and s.code_ref_hash not in allowed_hashes
    ]
    if bad_hashes:
        issues.append(
            f"subtopics use code_ref_hash not in allowed_hashes: {bad_hashes}. "
            f"Pick ONLY from the allowed_hashes list shown in the prompt."
        )

    # Code-density floor scaled to bank size. Each subtopic = 1 code block,
    # so the floor IS the subtopic count.
    n_allowed = len(allowed_hashes)
    n_used = len(draft.subtopics)
    if n_allowed >= 20:
        floor = 6
    elif n_allowed >= 10:
        floor = 4
    elif n_allowed >= 6:
        floor = 3
    elif n_allowed >= 3:
        floor = max(_SUBTOPICS_MIN, 3)
    else:
        floor = _SUBTOPICS_MIN
    if n_used < floor:
        sorted_bank = sorted(allowed_hashes)[:30]
        bank_listing = ", ".join(sorted_bank)
        if len(allowed_hashes) > 30:
            bank_listing += f", ... ({len(allowed_hashes) - 30} more)"
        issues.append(
            f"subtopics has only {n_used} entries but the section's code "
            f"bank offers {n_allowed} hashes — that's a CODE-FIRST violation. "
            f"Emit at least {floor} subtopics, each with a distinct hash "
            f"from the bank. Available hashes you can cite: [{bank_listing}]. "
            f"Each Subtopic needs subheading (2-10 words) + explanation "
            f"(8-80 words, the prose BEFORE the code) + code_ref_hash."
        )

    bad_sources = [
        c.source_key for c in draft.citations
        if c.source_key not in valid_source_keys
    ]
    if bad_sources:
        issues.append(
            f"citations use source_keys not in the digest: {bad_sources}. "
            f"Pick ONLY from the source_keys listed in the prompt."
        )

    # ── Ship B + E: subheading and explanation must ground to the code ──
    # Skipped when vault_rich is unavailable (back-compat with older callers).
    if vault_rich:
        # Derived subtopics have their own validation path (AST gate in
        # render_audit_write); skip the verbatim-anchor check for them.
        misaligned_sub: list[str] = []
        misaligned_expl: list[str] = []
        for s in draft.subtopics:
            if getattr(s, "code_source", "verbatim") == "derived":
                continue
            entry = vault_rich.get(s.code_ref_hash) if vault_rich else None
            if entry is None:
                continue
            # entry can be a dict-shaped VaultEntry or the model itself.
            body = (
                entry.get("fence_text") if isinstance(entry, dict)
                else getattr(entry, "fence_text", "")
            ) or ""
            if not body.strip():
                continue
            code_idents = _ast_identifiers(body)
            if not code_idents:
                # No identifiers extractable (e.g., directory tree or
                # plain markdown) — skip both alignment checks; let the
                # writer's heuristics handle it.
                continue

            # Ship E: subheading↔code. Strict-AST overlap first; if zero,
            # fall back to first-3-lines word overlap (catches less
            # tightly-named patterns like 'Minimal Tool Definition').
            sub_overlap = _prose_tokens(s.subheading) & code_idents
            if not sub_overlap:
                head_words = _first_lines_word_set(body, n_lines=3)
                head_overlap = {
                    w.lower() for w in _prose_tokens(s.subheading)
                } & head_words
                if not head_overlap:
                    misaligned_sub.append(s.subheading)

            # Ship B: explanation↔code. Stricter than subheading —
            # high-precision signal = an INLINE `code` span that names a
            # code identifier. Low-precision signal = bare word tokens.
            # A single bare-word overlap is too easy to game (every prose
            # mentioning "FastMCP" trivially passes), so the floor is:
            #   (any inline-backtick match) OR (≥2 distinct bare overlaps).
            inline_prose = set()
            for tk in re.findall(r"`([^`]+)`", s.explanation):
                for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", tk):
                    if w.lower() not in _IDENT_STOPWORDS and len(w) >= 3:
                        inline_prose.add(w)
            inline_match = inline_prose & code_idents
            if inline_match:
                pass  # high-precision signal — accept
            else:
                bare_overlap, _ = _identifier_overlap(s.explanation, body)
                if len(bare_overlap) < 2:
                    misaligned_expl.append(s.subheading)

        if misaligned_sub:
            sample = misaligned_sub[:3]
            issues.append(
                f"subheading↔code mismatch on subtopic(s) {sample!r}: the "
                f"subheading names a topic that has no overlap with the "
                f"chosen code_ref_hash body's identifiers. PICK THE HASH "
                f"FIRST, then name what the code actually demonstrates "
                f"(decorator, function, type, parameter visible in the "
                f"block). If no allowed hash matches the topic you want "
                f"to cover, drop that subtopic and pick a different hash."
            )
        if misaligned_expl:
            sample = misaligned_expl[:3]
            issues.append(
                f"explanation↔code mismatch on subtopic(s) {sample!r}: the "
                f"explanation references zero identifiers from the chosen "
                f"code block. Rewrite the explanation to name ≥1 specific "
                f"identifier (decorator like `@mcp.tool`, function name, "
                f"type, kwarg) that appears in the picked code body. "
                f"Generic prose that describes a broader topic without "
                f"grounding to the visible code is rejected."
            )

    return issues


# =============================================================================
# Picker fallback — structural scoring (Self-Certainty proxy)
# =============================================================================
def score_draft_structural(
    draft: _LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> float:
    """Deterministic structural quality score, used as a picker fallback
    when the critic LLM fails to return a parseable choice.

    Inspired by Self-Certainty (arXiv 2502.18581) — pick by a scalar
    quality estimate when no reward model is available. We don't have
    logprobs from the bandit rotator, so the proxy is structural:

      base = 5.0
      − 10 × n_vault_violations
      − 10 × n_citation_violations
      − 5  × (heading_mismatch ? 1 : 0)
      + 5  × min(citation_count / max(n_primary_contribs, 1), 1.0)
      + 3  × min(paragraph_count / 5, 1.0)
      − 2  × max(0, paragraph_count - 12)
      + 2  × min(total_chars / 1500, 2.0)

    Higher = better. Used in argmax-mode by the node.
    """
    issues = validate_section_against_inputs(
        draft,
        expected_heading=expected_heading,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        vault_rich=vault_rich,
    )
    # v2 cookbook scoring: subtopic count + explanation density + heading
    # match + citation count drive the structural score.
    n_vault_violations = sum(
        1 for s in draft.subtopics
        if s.code_ref_hash and s.code_ref_hash not in allowed_hashes
    )
    n_citation_violations = sum(
        1 for c in draft.citations if c.source_key not in valid_source_keys
    )
    heading_mismatch = (
        draft.heading.strip().casefold() != expected_heading.strip().casefold()
    )

    n_subtopics = len(draft.subtopics)
    n_citations = len(draft.citations)
    total_expl_chars = sum(len(s.explanation) for s in draft.subtopics)
    intro_chars = len(draft.intro or "")

    score = 5.0
    score -= 10.0 * n_vault_violations
    score -= 10.0 * n_citation_violations
    score -= 5.0 if heading_mismatch else 0.0
    # Reward 4-6 subtopics; penalize <3 (impossible — Pydantic blocks) or >10
    score += 5.0 * min(n_subtopics / 5.0, 1.0)
    score -= 1.0 * max(0, n_subtopics - 10)
    # Reward citation density
    if n_primary_contribs > 0:
        score += 4.0 * min(n_citations / n_primary_contribs, 1.0)
    # Reward intro + explanations in sweet spot
    if intro_chars >= 60:
        score += 1.0
    avg_expl = total_expl_chars / max(1, n_subtopics)
    if 60 <= avg_expl <= 400:
        score += 2.0
    elif avg_expl > 800:
        score -= 1.0
    return round(score, 3)


# =============================================================================
# Coverage stats (deterministic aggregate)
# =============================================================================
def compute_sawc_stats(
    sections: list[Section],
    n_stages: int,
    n_total_drafts_fired: int,
    n_critic_picks: int,
    n_picker_fallbacks: int,
) -> SAWCStats:
    n_sections = len(sections)
    # R3 (2026-05-26 late evening) — relaxed `n_sections_completed` from
    # "zero issues" to "content-bearing." The previous definition counted
    # only sections with EMPTY `issues` lists; in Run 3 this meant 0-2
    # of 12-30 sections per chapter were counted as "completed" because
    # most ship with soft warnings (subheading↔code identifier mismatch,
    # subtopic-count shy of code-bank size). Those warnings describe
    # quality nudges, not "section failed to write" — the chapter has
    # the section, with content, in the right order. The checklist gate
    # `check_all_sections_present` should fail only when sections are
    # ABSENT (placeholders or missing content), not when they're
    # imperfect.
    def _is_present(s) -> bool:
        if "placeholder" in (s.issues or []):
            return False
        if not (s.heading or "").strip():
            return False
        if not (s.intro or "").strip():
            return False
        if not s.subtopics:
            return False
        if not s.citations:
            return False
        return True

    n_sections_completed = sum(1 for s in sections if _is_present(s))
    n_sections_fallback = sum(1 for s in sections if "placeholder" in s.issues)
    n_repairs = sum(s.n_repairs for s in sections)
    total_subtopics = sum(len(s.subtopics) for s in sections)
    total_citations = sum(len(s.citations) for s in sections)
    total_expl_words = sum(
        len((st.explanation or "").split())
        for s in sections for st in s.subtopics
    )
    return SAWCStats(
        n_sections=n_sections,
        n_sections_completed=n_sections_completed,
        n_sections_fallback=n_sections_fallback,
        n_stages=n_stages,
        n_total_drafts_fired=n_total_drafts_fired,
        n_critic_picks=n_critic_picks,
        n_picker_fallbacks=n_picker_fallbacks,
        n_repairs=n_repairs,
        total_subtopics=total_subtopics,
        total_citations=total_citations,
        avg_subtopics_per_section=(
            total_subtopics / n_sections if n_sections else 0.0
        ),
        avg_explanation_words=(
            total_expl_words / total_subtopics if total_subtopics else 0.0
        ),
    )


# =============================================================================
# Prompt templates
# =============================================================================
def _format_contributions_block(contributions: list[dict]) -> str:
    """Pretty-format the digest's per_section[section_id] contributions for
    the writer prompt."""
    if not contributions:
        return "(no contributions assigned to this section — write a thin "\
               "orientation paragraph only; checklist_eval will flag this)"
    lines: list[str] = []
    for i, c in enumerate(contributions):
        src = c.get("source_key") or "?"
        # Source key can be long — show last component
        src_short = src.rsplit("/", 1)[-1]
        relevance = c.get("relevance", "?")
        summary = c.get("summary", "")
        facts = c.get("key_facts") or []
        refs = c.get("code_refs") or []
        lines.append(
            f"  [{i + 1}] {src_short} ({relevance}) — {summary}\n"
            f"      key_facts:"
        )
        for f in facts[:5]:
            lines.append(f"        • {f}")
        if refs:
            lines.append(f"      code_refs: {', '.join(refs)}")
    return "\n".join(lines)


def _format_memory_block(memory: list[dict]) -> str:
    """Pretty-format the memory ledger for the writer prompt.

    `memory` is a list of MemoryEntry-shaped dicts (we accept dicts so
    callers can pass either model_dump() results or raw structures)."""
    if not memory:
        return "  (this is the first stage — no prior sections yet)"
    lines: list[str] = []
    for e in memory:
        sid = e.get("section_id", "?")
        head = e.get("heading", "?")
        summ = e.get("summary", "")
        terms = e.get("key_terminology") or []
        lines.append(f"  [{sid}] {head}")
        lines.append(f"      summary:     {summ}")
        if terms:
            lines.append(
                f"      terminology: {', '.join(terms)}"
            )
    return "\n".join(lines)


def build_writer_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
    prose_mode: bool = False,
    already_shown_hashes: set[str] | None = None,
) -> str:
    """Build the per-section per-draft writer prompt.

    Args:
        vault_rich: optional dict[hash → VaultEntry-like dict] giving the
            LLM full visibility into each allowed code block (Visible Vault,
            2026-05-24 Ship #1). When provided, renders `<code id=...>{body}
            </code>` envelopes so the LLM can pick pedagogically valuable
            hashes from informed context. When None, falls back to plain
            hash listing (legacy behavior).
        prose_mode: PROSE PATH (2026-05-30). True for a conceptual section
            with NO code in its sources (empty bank). The writer emits prose
            subtopics (empty code_ref_hash) instead of failing to a
            placeholder. Auto-enabled when allowed_hashes is empty.
        already_shown_hashes: cross-section anti-recycling (Fix #3). Hashes a
            PRIOR section of this chapter already turned into a subtopic — the
            writer is told to reference, not re-show, them.
    """
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites
        else "(none — this is a stage-0 section)"
    )
    # A section with an empty code bank is a conceptual/prose topic — write
    # prose subtopics rather than failing to an empty placeholder.
    prose = prose_mode or not allowed_hashes

    # Fix #3 — cross-section recycling: list the canonical code already shown
    # earlier in THIS chapter so the writer references instead of re-emitting.
    already_shown_hashes = already_shown_hashes or set()
    shown_here = sorted(h for h in (already_shown_hashes or set()) if h)
    already_shown_block = ""
    if shown_here and not prose:
        listing = ", ".join(shown_here[:40])
        if len(shown_here) > 40:
            listing += f", … ({len(shown_here) - 40} more)"
        already_shown_block = (
            f"== ALREADY SHOWN EARLIER IN THIS CHAPTER (do NOT re-pick) ==\n"
            f"These hashes were already rendered as subtopics in earlier "
            f"sections. Re-picking one makes this section a hollow 'see above' "
            f"cross-reference (a render-time pass strips the duplicate). PREFER "
            f"hashes NOT in this list; only re-pick if it is genuinely central "
            f"to THIS section's distinct angle:\n  {listing}\n\n"
        )

    # Visible Vault rendering — see Ship #1 in
    # docs/KD-CODE-FIRST-SOTA-2026-05-24.md. The LLM sees the FULL code
    # body (no truncation, per feedback_kd_quality_over_speed) so it can
    # pick canonical examples and write tight commentary. The renderer
    # substitutes the vault entry verbatim at render time, so prompt-side
    # variance doesn't affect output fidelity.
    if allowed_hashes and vault_rich:
        from ..vault.service import format_entry_for_prompt
        from ..vault.types import VaultEntry as _VaultEntry

        envelopes: list[str] = []
        for h in allowed_hashes:
            entry = vault_rich.get(h)
            if entry is None:
                envelopes.append(f'<code id="{h}" missing="true"/>')
                continue
            # Coerce dict → VaultEntry if needed for type compatibility.
            if isinstance(entry, dict):
                try:
                    entry = _VaultEntry(**entry)
                except Exception:
                    envelopes.append(
                        f'<code id="{h}" lang="{entry.get("lang","text")}">\n'
                        f'{entry.get("fence_text") or ""}\n'
                        f'</code>'
                    )
                    continue
            envelopes.append(format_entry_for_prompt(entry))
        hash_list = "\n\n".join(envelopes)
    else:
        hash_list = (
            "\n".join(f"  - {h}" for h in allowed_hashes)
            if allowed_hashes
            else "  (none — prose-only section, leave code_refs empty)"
        )

    source_list = (
        "\n".join(f"  - {k}" for k in valid_source_keys)
        if valid_source_keys
        else "  (no sources — citations may be empty)"
    )

    # Prose vs code-first: build the bank section + a top-of-prompt directive.
    if prose:
        prose_note = (
            "🟦 PROSE MODE — this section's sources have NO code; it is a "
            "CONCEPTUAL topic. The CODE-FIRST rules below (pick a hash first, "
            "code-density, identifier grounding, no-recycle) are SUSPENDED. "
            "Set EVERY subtopic's code_ref_hash to \"\" (empty) and write "
            "substantial, source-grounded conceptual prose. Still emit ≥3 "
            "DISTINCT subtopics and ground each to the contributions / "
            "citations below — state only what the sources say.\n\n"
        )
        bank_section = (
            "== PROSE SECTION — NO CODE BANK ==\n"
            "Teach this concept as prose. Emit 3-6 subtopics, each:\n"
            "  - code_ref_hash: \"\"   (EMPTY — no code to anchor)\n"
            "  - subheading: 2-10 words naming the concept / step / policy\n"
            "  - explanation: a substantial 40-80 word paragraph that "
            "actually TEACHES it, grounded in the contributions + citations "
            "(no invented specifics — no numbers, flags, or APIs the sources "
            "don't state).\n"
        )
    else:
        prose_note = ""
        bank_section = (
            f"== ALLOWED CODE BANK ({len(allowed_hashes)} entries) — these "
            f"are the actual code blocks available for THIS section. "
            f"Each `<code id=...>` envelope shows the FULL code body. PICK "
            f"3-8 BEST ONES — each becomes one subtopic. Reason about each "
            f"block fully; the explanation must reference specific lines / "
            f"decorators / arguments. ==\n"
            f"{hash_list}"
        )
    return (
        f"You are the Section Writer — step 6 of the Docs Distiller "
        f"synth pipeline. Write ONE section of one chapter as a "
        f"COOKBOOK — a sequence of (subheading, explanation, code block) "
        f"triples. This is one of N=3 best-of-N drafts; a critic LLM "
        f"will pick the best afterwards (MAMM-Refine arXiv 2503.15272).\n\n"

        f"⚡ CRITICAL PURPOSE — this is a CODE-FIRST learning resource. "
        f"The reader is here to learn {framework} FAST by reading "
        f"production-quality code with focused explanations. Structure "
        f"is: TOPIC (H2) → SUBTOPIC (H3) → 1-2 sentence explanation → "
        f"code block. Repeat the subtopic pattern 4-6 times per "
        f"section. Each code block teaches ONE pedagogically valuable "
        f"thing.\n\n"

        f"{prose_note}"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION (H2): {section_id} — {section_heading}\n"
        f"SECTION GOAL: {section_description}\n"
        f"PREREQUISITES (already covered): {prereqs_str}\n\n"

        f"== GROUNDED CONTRIBUTIONS (your subtopics MUST cover these) ==\n"
        f"{_format_contributions_block(contributions)}\n\n"

        f"{already_shown_block}"

        f"{bank_section}\n\n"

        f"== VALID CITATION SOURCE_KEYS ({len(valid_source_keys)}) — "
        f"these are the source docs that the digest routed TO THIS "
        f"SECTION specifically (NOT chapter-wide). "
        f"citations.source_key MUST be one of these — citing a source "
        f"that wasn't routed here means the section is straying from "
        f"its assigned scope. ==\n"
        f"{source_list}\n\n"

        f"== MEMORY (compressed prior-stage sections — already covered, "
        f"don't re-introduce) ==\n"
        f"{_format_memory_block(memory)}\n\n"

        f"== OUTPUT — strict JSON (cookbook v2 schema, code-first order) ==\n"
        f"{{\n"
        f'  "heading":  "{section_heading}",  /* ECHO verbatim, no "# " */\n'
        f'  "intro":    "1-2 sentences (20-400 chars) framing what this '
        f'section covers and why the reader should care. NO code fences.",\n'
        f'  "subtopics": [\n'
        f'    {{\n'
        f'      "code_ref_hash": "16-hex hash — PICK THIS FIRST from the '
        f'code bank above; the next two fields describe THIS chosen block",\n'
        f'      "subheading":    "2-10 word phrase NAMING what the chosen '
        f'code block demonstrates (derive from its identifiers/decorators '
        f'/function names — NOT the broader topic you might want to '
        f'cover).",\n'
        f'      "explanation":   "8-80 words describing the chosen block. '
        f'MUST mention ≥1 specific identifier (decorator, function name, '
        f'type, parameter) that is visible in the code body. NO code '
        f'fences inside."\n'
        f'    }},\n'
        f'    ... 3-12 subtopics, aim for 4-6 ...\n'
        f'  ],\n'
        f'  "citations": [\n'
        f'    {{"source_key": "ingestion/.../0024-foo.md", '
        f'"claim": "restate the specific fact this source backs"}},\n'
        f'    ...\n'
        f'  ]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. `heading` MUST be EXACTLY {section_heading!r} (case-sensitive "
        f"   echo). No leading '#' chars.\n"
        f"2. **Per subtopic: PICK code_ref_hash FIRST, then write the "
        f"   subheading + explanation that ground to THAT block's actual "
        f"   identifiers**. Do NOT pick a topic-sounding subheading and "
        f"   then grab a random hash — that produces prose that doesn't "
        f"   describe the code below it (hard fail in the validator).\n"
        f"3. Each subtopic MUST have a UNIQUE code_ref_hash from the bank "
        f"   above. Inventing or paraphrasing a hash is a hard violation.\n"
        f"4. **CODE DENSITY: at least 3 subtopics per section. Aim for "
        f"   4-6** when the bank has ≥6 entries; up to 8 when bank ≥20. "
        f"   The whole point is code-rich learning material.\n"
        f"5. **EXPLANATION GROUNDING (Ship B, validator-enforced)**: the "
        f"   explanation MUST reference ≥1 identifier visible in the chosen "
        f"   code body — a decorator name, function name, type, kwarg, or "
        f"   imported symbol. Generic topic prose with zero code-anchored "
        f"   terms is rejected.\n"
        f"6. **NO EMBELLISHMENT (U3, 2026-05-27)**: describe ONLY what the "
        f"   chosen code body SHOWS or what the section's source digest "
        f"   explicitly says. Do NOT invent: parameter names not in the "
        f"   code, default values not stated, return types not annotated, "
        f"   error classes not raised in the snippet, related APIs not "
        f"   imported, command-line flags not appearing in the block, "
        f"   pricing/quota/SLA facts the source doesn't state. Anti-"
        f"   examples (DO NOT WRITE these unless the code/source contains "
        f"   them verbatim):\n"
        f"     ✗ 'The Browser class provides methods for creating new "
        f"       pages, retrieving all pages, and closing the session' — "
        f"       UNLESS the code shows these three methods.\n"
        f"     ✗ 'The @sandbox decorator accepts a max_steps parameter to "
        f"       limit agent loop iterations' — UNLESS `max_steps=` "
        f"       appears in the code block.\n"
        f"     ✗ 'Browser Use's pricing is $X per Y' — UNLESS the digest "
        f"       contains pricing facts.\n"
        f"   The atomic-claim grounding judge AND the CoCoA alignment "
        f"   judge BOTH flag embellishment; chapters fail when these run "
        f"   above threshold. Stay strictly inside what's visible. If you "
        f"   want to teach something the code doesn't show, PICK A "
        f"   DIFFERENT HASH that does show it.\n"
        f"7. **SUBHEADING GROUNDING (Ship E, validator-enforced)**: the "
        f"   subheading MUST share ≥1 token with the code body's identifiers "
        f"   OR with words in its first 3 non-blank lines. 'Token Caching "
        f"   to Reduce Verification Overhead' is REJECTED when the code "
        f"   shows `@mcp.tool def write_summary(...)` — those mention "
        f"   nothing about token caching. Pick the hash first, name what "
        f"   it actually demonstrates.\n"
        f"8. EXPLANATIONS ARE TIGHT: 8-80 words. Reference specific lines/"
        f"   decorators/types from the chosen code. NO multi-paragraph "
        f"   summaries.\n"
        f"9. DISTINCT subheadings within the section — no two subtopics "
        f"   can share a subheading or share a code_ref_hash.\n"
        f"9b. **NO BOILERPLATE RECYCLING (DD-SYNTH-SECTION-RECYCLING-"
        f"    2026-05-29)**: do NOT center a subtopic on a generic canonical "
        f"    artifact — a full class/dataclass definition, a complete "
        f"    config-file dump, or import boilerplate — when a more "
        f"    SECTION-SPECIFIC hash is available. Those same blocks get "
        f"    picked by sibling sections and make the chapter repetitive. A "
        f"    render-time pass REMOVES any code block whose body duplicates "
        f"    one already shown earlier in the chapter (replacing it with a "
        f"    cross-reference), so a recycled pick wastes the subtopic. "
        f"    Choose hashes that demonstrate THIS section's distinct angle.\n"
        f"10. Every `citations[*].source_key` MUST be one of the valid "
        f"    source_keys above. Aim for {n_primary_contribs}+ citations.\n"
        f"11. NO inline `<code-ref hash=\"...\"/>` tags anywhere. NO "
        f"    ```code fences``` in `intro` or `explanation`. The renderer "
        f"    materializes code per-subtopic from `code_ref_hash`.\n"
        f"12. NO `# docs:` / `# src:` source-id leaks in prose. Use the "
        f"    typed `citations` field.\n"
        f"13. Don't re-introduce terminology already in `memory[*]"
        f".key_terminology` above — assume the reader saw it.\n"
        f"14. PEDAGOGICAL ORDER: subtopics ordered easiest → most "
        f"    advanced. First subtopic = canonical/minimal example. "
        f"    Subsequent subtopics = primitives / recipes / edge cases.\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation."
    )


def build_critic_picker_prompt(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    candidates_summary: list[dict],
) -> str:
    """Build the MAMM-Refine-style critic picker prompt.

    The critic sees only structural summaries of each candidate (counts +
    violation flags + headings), NOT the full prose — matches outline_sdp's
    USC pattern. Per MAMM-Refine §4: 'reranking > regeneration'."""
    lines: list[str] = []
    for i, c in enumerate(candidates_summary):
        violations = c.get("violations") or []
        viol_str = (
            f" violations=({len(violations)}: " + "; ".join(violations[:3]) + ")"
            if violations
            else " violations=(none)"
        )
        lines.append(
            f"  [{i}] subtopics={c.get('n_subtopics')}, "
            f"intro_chars={c.get('intro_chars')}, "
            f"avg_expl_words={c.get('avg_expl_words', 0):.0f}, "
            f"citations={c.get('n_citations')}, "
            f"heading_match={'✓' if c.get('heading_match') else '✗'}, "
            f"structural_score={c.get('structural_score', 0):.2f}"
            f"{viol_str}"
        )
    candidates_block = "\n".join(lines)
    return (
        f"You are the Critic-Picker for section {section_id} "
        f"({section_heading!r}). Pick the SINGLE BEST draft from "
        f"{len(candidates_summary)} candidates. Per MAMM-Refine "
        f"(arXiv 2503.15272), this rerank step outperforms regenerating; "
        f"choose deliberately by the rubric below — IN ORDER.\n\n"

        f"Rubric (apply top-down — a higher-priority criterion decides "
        f"ties on lower ones):\n"
        f"1. ZERO violations (subtopic hashes outside allowed, citations "
        f"   outside valid source_keys, heading mismatch). A candidate "
        f"   with any violations LOSES to any clean candidate.\n"
        f"2. Subtopic count in sweet spot: 4-6 subtopics is ideal; "
        f"   3 is acceptable; 7-8 is OK for content-heavy sections.\n"
        f"3. Citation count near or above n_primary_contribs="
        f"{n_primary_contribs} (one citation per primary contribution).\n"
        f"4. Average explanation words 15-60 (concise per subtopic).\n"
        f"5. Highest structural_score (a deterministic proxy combining "
        f"   the above — useful as a tiebreaker).\n\n"

        f"Candidates:\n{candidates_block}\n\n"

        f"Respond ONLY with valid JSON: {{\"chosen_index\": <int>}} "
        f"where the integer is 0..{len(candidates_summary) - 1}. "
        f"No prose, no explanation."
    )


def build_repair_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    current_json: str,
    issues: list[str],
    prose_mode: bool = False,
) -> str:
    """Repair prompt — same context as writer prompt, plus the
    issue list, asking for a fixed version preserving good fields."""
    prose = prose_mode or not allowed_hashes
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites else "(none)"
    )
    hash_list = (
        "PROSE MODE — this section has NO code. Set EVERY subtopic's "
        "code_ref_hash to \"\" (empty) and write substantial source-grounded "
        "conceptual prose (3-6 distinct subtopics, 40-80 word explanations)."
        if prose
        else (
            "\n".join(f"  - {h}" for h in allowed_hashes)
            if allowed_hashes else "  (none)"
        )
    )
    source_list = (
        "\n".join(f"  - {k}" for k in valid_source_keys)
        if valid_source_keys else "  (none)"
    )
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this cookbook-schema section draft. "
        f"Keep the same v2 schema (heading + intro + subtopics + citations). "
        f"Preserve good subtopics and citations; ONLY change what's "
        f"needed to clear the issues below.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION: {section_id} — {section_heading}\n"
        f"GOAL: {section_description}\n"
        f"PREREQUISITES: {prereqs_str}\n\n"

        f"ALLOWED VAULT HASHES (use ONLY these for subtopics[*].code_ref_hash):\n"
        f"{hash_list}\n\n"

        f"VALID CITATION SOURCE_KEYS (use ONLY these for citations):\n"
        f"{source_list}\n\n"

        f"CONTRIBUTIONS (for grounding):\n"
        f"{_format_contributions_block(contributions)}\n\n"

        f"MEMORY:\n{_format_memory_block(memory)}\n\n"

        f"CURRENT DRAFT:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Schema reminder: {{\n"
        f'  "heading": "...",\n'
        f'  "intro": "1-2 sentence section framing",\n'
        f'  "subtopics": [\n'
        f'    {{"subheading": "2-10 words", "explanation": "8-80 words", '
        f'"code_ref_hash": "{"" if prose else "16-hex"}"}},\n'
        f'    ... 3-12 entries ...\n'
        f'  ],\n'
        f'  "citations": [{{"source_key": "...", "claim": "..."}}, ...]\n'
        f"}}\n\n"

        f"Respond ONLY with valid JSON matching the v2 schema. "
        f"NO commentary, NO markdown wrapping."
    )


# =============================================================================
# Candidate summarization for the critic prompt
# =============================================================================
def summarize_candidate(
    draft: _LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> dict:
    """Compact structural summary of one candidate draft for the critic
    picker. Keeps the picker context small (~250 tokens per candidate)
    and biases the decision toward STRUCTURE, not content (per
    outline_sdp's same-pattern argument)."""
    issues = validate_section_against_inputs(
        draft,
        expected_heading=expected_heading,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        vault_rich=vault_rich,
    )
    n_subtopics = len(draft.subtopics)
    total_expl_words = sum(
        len((s.explanation or "").split()) for s in draft.subtopics
    )
    avg_expl_words = (total_expl_words / n_subtopics) if n_subtopics else 0.0
    intro_chars = len(draft.intro or "")
    structural_score = score_draft_structural(
        draft,
        expected_heading=expected_heading,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        n_primary_contribs=n_primary_contribs,
        vault_rich=vault_rich,
    )
    return {
        "n_subtopics":      n_subtopics,
        "intro_chars":      intro_chars,
        "avg_expl_words":   avg_expl_words,
        "n_citations":      len(draft.citations),
        "heading_match":    (
            draft.heading.strip().casefold()
            == expected_heading.strip().casefold()
        ),
        "structural_score": structural_score,
        "violations":       issues,
    }
