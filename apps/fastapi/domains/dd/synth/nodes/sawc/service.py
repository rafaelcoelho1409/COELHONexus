"""SAWC — Section-Aware Writer-Critic for one chapter.

v2 cookbook schema: {heading, intro, subtopics: [{subheading, explanation,
code_ref_hash}], citations}. Each subtopic renders as H3 + 1-2 sentence
prose + ONE code block. Best-of-N drafts + critic-picker (MAMM-Refine);
2-attempt repair loop fixes alignment violations."""
from __future__ import annotations
from .keys import (
    digest_latest_key,
    digest_latest_key as _digest_latest_key,
    latest_blob_key,
    latest_blob_key as _latest_blob_key,
    outline_latest_key,
    outline_latest_key as _outline_latest_key,
    versioned_blob_key,
    versioned_blob_key as _versioned_blob_key,
)
from .params import (
    CITATION_CLAIM_CHARS_MAX,
    CITATION_CLAIM_CHARS_MIN,
    CITATIONS_MAX,
    CITATIONS_MIN,
    CODE_REFS_MAX,
    EXPLANATION_WORDS_MAX,
    EXPLANATION_WORDS_MIN,
    HEADING_MAX_WORDS,
    HEADING_MIN_WORDS,
    INTRO_CHARS_MAX,
    INTRO_CHARS_MIN,
    MAX_REPAIR_ATTEMPTS,
    MEMORY_SUMMARY_CHARS_MAX,
    MEMORY_SUMMARY_CHARS_MIN,
    MEMORY_TERM_CHARS_MAX,
    MEMORY_TERM_CHARS_MIN,
    MEMORY_TERMS_MAX,
    MEMORY_TERMS_MIN,
    N_DRAFTS,
    N_DRAFTS as _N_DRAFTS,
    PARAGRAPH_CHARS_MAX,
    PARAGRAPH_CHARS_MIN,
    PARAGRAPHS_MAX,
    PARAGRAPHS_MIN,
    PLACEMENT_HINT_CHARS_MAX,
    PLACEMENT_HINT_CHARS_MIN,
    SUBHEADING_MAX_WORDS,
    SUBHEADING_MIN_WORDS,
    SUBTOPICS_MAX,
    SUBTOPICS_MIN,
)
from .patterns import HASH_RE, SECTION_ID_RE
from .schemas import (
    ChapterDraft,
    Citation,
    LLMSectionDraft,
    LLMSectionDraft as _LLMSectionDraft,
    MemoryEntry,
    SAWCStats,
    Section,
    Subtopic,
)
from .versions import SAWC_PROMPT_VERSION, SAWC_SCHEMA_VERSION

import ast
import asyncio
import json
import logging
import os
import re
import time
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from domains.llm.rotator.chain import chat_judge_bandit_async
from domains.llm.rotator.chain.domain import is_heavyweight as _sawc_writer_filter

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState
from ..render.keys import source_key_to_vault_key as _source_key_to_vault_key
from ..vault.domain import format_entry_for_prompt
from ..vault.domain import rank_hashes_by_pedagogy as _rank_hashes_by_pedagogy
from ..vault.schemas import VaultEntry


logger = logging.getLogger(__name__)


# Code-body identifier extraction (Ship B + E)
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


# Deterministic memory extraction (v1: no extra LLM call)
def extract_memory_entry(
    section: Section,
    section_contributions: list[dict],
    section_heading: str,
) -> MemoryEntry:
    """Derive a compressed MemoryEntry from a freshly-written section
    plus its digest contributions.

    v1 strategy (deterministic — saves N extra LLM calls per chapter):
      - summary: first paragraph of the section, trimmed to fit
                  MEMORY_SUMMARY_CHARS_MAX
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
    if len(summary) > MEMORY_SUMMARY_CHARS_MAX:
        summary = summary[: MEMORY_SUMMARY_CHARS_MAX - 1].rsplit(" ", 1)[0] + "…"
    if len(summary) < MEMORY_SUMMARY_CHARS_MIN:
        # Pad with the heading + a generic phrase so the Pydantic min
        # passes; mgsr_replan will flag thin sections via checklist_eval
        summary = (
            f"{section_heading}: {summary}"
            if summary
            else f"{section_heading}: (no content)"
        )
        if len(summary) < MEMORY_SUMMARY_CHARS_MIN:
            summary = summary + " — content pending refinement."

    # --- terminology: extract code-ish identifiers from key_facts ---
    candidates: list[str] = []
    for contrib in section_contributions or []:
        for fact in (contrib.get("key_facts") or []):
            # Pull `inline_code` spans
            for m in re.finditer(r"`([^`]+)`", fact):
                t = m.group(1).strip()
                if 2 <= len(t) <= MEMORY_TERM_CHARS_MAX:
                    candidates.append(t)
            # Pull capitalized identifiers (PascalCase or camelCase)
            for m in re.finditer(r"\b([A-Z][a-zA-Z0-9_]{2,})\b", fact):
                t = m.group(1).strip()
                if 3 <= len(t) <= MEMORY_TERM_CHARS_MAX:
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
        if len(terminology) >= MEMORY_TERMS_MAX:
            break

    return MemoryEntry(
        section_id = section.section_id,
        heading = section_heading,
        summary = summary,
        key_terminology = terminology,
    )


# Cross-reference validators (post-Pydantic, fail-soft for repair loop)
# Soft issues (quality nudges) report via .issues but don't trigger the
# repair loop — the LLM can't reliably close them and burns budget retrying.
# Hard issues (heading drift, hallucinated hash/source) still repair.
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
    draft: LLMSectionDraft,
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
        floor = max(SUBTOPICS_MIN, 3)
    else:
        floor = SUBTOPICS_MIN
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
                head_words = _first_lines_word_set(body, n_lines = 3)
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


# Picker fallback — structural scoring (Self-Certainty proxy)
def score_draft_structural(
    draft: LLMSectionDraft,
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
        expected_heading = expected_heading,
        allowed_hashes = allowed_hashes,
        valid_source_keys = valid_source_keys,
        vault_rich = vault_rich,
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


# Coverage stats (deterministic aggregate)
def compute_sawc_stats(
    sections: list[Section],
    n_stages: int,
    n_total_drafts_fired: int,
    n_critic_picks: int,
    n_picker_fallbacks: int,
) -> SAWCStats:
    n_sections = len(sections)
    # `n_sections_completed` = content-bearing, NOT "zero issues" — soft
    # warnings still ship the section; gate fires only on actual absence.
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
        n_sections = n_sections,
        n_sections_completed = n_sections_completed,
        n_sections_fallback = n_sections_fallback,
        n_stages = n_stages,
        n_total_drafts_fired = n_total_drafts_fired,
        n_critic_picks = n_critic_picks,
        n_picker_fallbacks = n_picker_fallbacks,
        n_repairs = n_repairs,
        total_subtopics = total_subtopics,
        total_citations = total_citations,
        avg_subtopics_per_section = (
            total_subtopics / n_sections if n_sections else 0.0
        ),
        avg_explanation_words = (
            total_expl_words / total_subtopics if total_subtopics else 0.0
        ),
    )


# Prompt templates
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


try:
    from infra.langfuse.prompts import with_langfuse_override as _lf_override
except Exception:
    _lf_override = lambda *a, **kw: (lambda fn: fn)  # noqa: E731


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
            2026-05-24 Ship #1). When provided, renders `<code id = ...>{body}
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

    # Visible vault — LLM sees full code bodies; render still substitutes
    # via hash so output is byte-perfect.
    if allowed_hashes and vault_rich:
        from ..vault.domain import format_entry_for_prompt
        from ..vault.schemas import VaultEntry as _VaultEntry

        envelopes: list[str] = []
        for h in allowed_hashes:
            entry = vault_rich.get(h)
            if entry is None:
                envelopes.append(f'<code id = "{h}" missing = "true"/>')
                continue
            # Coerce dict → VaultEntry if needed for type compatibility.
            if isinstance(entry, dict):
                try:
                    entry = _VaultEntry(**entry)
                except Exception:
                    envelopes.append(
                        f'<code id = "{h}" lang = "{entry.get("lang","text")}">\n'
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
            f"Each `<code id = ...>` envelope shows the FULL code body. PICK "
            f"3-8 BEST ONES — each becomes one subtopic. Reason about each "
            f"block fully; the explanation must reference specific lines / "
            f"decorators / arguments. ==\n"
            f"{hash_list}"
        )
    return (
        f"You are the Section Writer — step 6 of the Docs Distiller "
        f"synth pipeline. Write ONE section of one chapter as a "
        f"COOKBOOK — a sequence of (subheading, explanation, code block) "
        f"triples. This is one of N = 3 best-of-N drafts; a critic LLM "
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
        f"       limit agent loop iterations' — UNLESS `max_steps = ` "
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
        f"11. NO inline `<code-ref hash = \"...\"/>` tags anywhere. NO "
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
            f" violations = ({len(violations)}: " + "; ".join(violations[:3]) + ")"
            if violations
            else " violations = (none)"
        )
        lines.append(
            f"  [{i}] subtopics = {c.get('n_subtopics')}, "
            f"intro_chars = {c.get('intro_chars')}, "
            f"avg_expl_words = {c.get('avg_expl_words', 0):.0f}, "
            f"citations = {c.get('n_citations')}, "
            f"heading_match = {'✓' if c.get('heading_match') else '✗'}, "
            f"structural_score = {c.get('structural_score', 0):.2f}"
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
        f"3. Citation count near or above n_primary_contribs = "
        f"{n_primary_contribs} (one citation per primary contribution).\n"
        f"4. Average explanation words 15-60 (concise per subtopic).\n"
        f"5. Highest structural_score (a deterministic proxy combining "
        f"   the above — useful as a tiebreaker).\n\n"

        f"Candidates:\n{candidates_block}\n\n"

        f"Respond ONLY with valid JSON: {{\"chosen_index\": <int>}} "
        f"where the integer is 0..{len(candidates_summary) - 1}. "
        f"No prose, no explanation."
    )


@_lf_override("dd.synth.sawc.repair")
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


# Candidate summarization for the critic prompt
def summarize_candidate(
    draft: LLMSectionDraft,
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
        expected_heading = expected_heading,
        allowed_hashes = allowed_hashes,
        valid_source_keys = valid_source_keys,
        vault_rich = vault_rich,
    )
    n_subtopics = len(draft.subtopics)
    total_expl_words = sum(
        len((s.explanation or "").split()) for s in draft.subtopics
    )
    avg_expl_words = (total_expl_words / n_subtopics) if n_subtopics else 0.0
    intro_chars = len(draft.intro or "")
    structural_score = score_draft_structural(
        draft,
        expected_heading = expected_heading,
        allowed_hashes = allowed_hashes,
        valid_source_keys = valid_source_keys,
        n_primary_contribs = n_primary_contribs,
        vault_rich = vault_rich,
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



# === SAWC-helpers restored from old commit (2026-06-07) ===
_CONCURRENCY           = 8

async def _load_chapter_vault_rich(
    minio,
    slug: str,
    source_keys: list[str],
) -> tuple[dict[str, VaultEntry], int, int]:
    """Returns (vault, n_loaded, n_skipped). Each value in `vault` is a
    VaultEntry — not just the fence text — so writer prompts can render
    full visible envelopes with lang + line_count metadata.

    Resolution per source (mirrors digest's read-time fallback so both
    nodes have identical vault visibility):
      1. Pre-built per-source vault file at `synth-vault/{slug}/pages/...`
      2. Runtime sentinelization of the raw ingestion page (preferred
         fallback when the consolidated llms-full crawl built only one
         mega-vault and individual per-page vaults are missing)
    """
    from ..vault.domain import sentinelize_doc as _sentinelize_doc

    rich_vault: dict[str, VaultEntry] = {}
    n_loaded = 0
    n_skipped = 0
    for source_key in source_keys:
        # Try the pre-built per-source vault first.
        vault_key = _source_key_to_vault_key(source_key, slug)
        used_runtime = False
        if await minio.exists(vault_key):
            try:
                text = await minio.read_text(vault_key)
                manifest = json.loads(text)
                entries = (manifest or {}).get("entries") or {}
                for h, entry_dict in entries.items():
                    if not isinstance(entry_dict, dict):
                        continue
                    try:
                        rich_vault[h] = VaultEntry(**entry_dict)
                    except Exception:
                        if entry_dict.get("fence_text"):
                            rich_vault[h] = VaultEntry(
                                hash=h,
                                fence_text=entry_dict.get("fence_text", ""),
                                info_string=entry_dict.get("info_string", ""),
                                lang=entry_dict.get("lang", ""),
                                line_count=int(entry_dict.get("line_count") or 0),
                                char_count=int(entry_dict.get("char_count") or 0),
                                sentinel_kind=entry_dict.get(
                                    "sentinel_kind", "fence_backtick",
                                ),
                            )
                n_loaded += 1
                continue
            except Exception as e:
                logger.warning(
                    f"[sawc_write] vault {vault_key!r} unreadable: "
                    f"{type(e).__name__}: {e} — falling back to runtime"
                )
                used_runtime = True
        else:
            used_runtime = True

        # Runtime fallback: read raw ingestion page + sentinelize on-the-fly.
        # This is the path the fastmcp/etc corpora use today because
        # ingestion only built one consolidated vault for llms-full.
        if used_runtime:
            try:
                raw = await minio.read_text(source_key)
                if not raw or "<code-ref hash=" in raw:
                    n_skipped += 1
                    continue
                _, entries = _sentinelize_doc(raw)
                if entries:
                    for h, e in entries.items():
                        if h not in rich_vault:
                            rich_vault[h] = e
                    n_loaded += 1
                else:
                    n_skipped += 1
            except Exception as e:
                n_skipped += 1
                logger.warning(
                    f"[sawc_write] runtime-sentinelize failed for "
                    f"{source_key!r}: {type(e).__name__}: {e}"
                )
    return rich_vault, n_loaded, n_skipped

def _dedupe_vault_hashes_across_sections(
    per_section_index: dict[str, list[dict]],
) -> tuple[int, int]:
    """Modify `per_section_index` in-place so each vault hash appears in
    at most one section. Returns (n_hashes_deduped, n_refs_removed).

    `n_hashes_deduped` = how many distinct hashes had ≥2 section claims
    `n_refs_removed`   = total code_ref entries removed (one hash can be
                         removed from multiple losing sections; this is the
                         sum over all losing sections)
    """
    from collections import defaultdict

    # Pass 1: for each (hash, section), find the BEST relevance any
    # contribution in that section asserts for the hash.
    hash_section_best_rel: dict[tuple[str, str], str] = {}
    for sid, contribs in per_section_index.items():
        for c in contribs:
            rel = c.get("relevance") or "tangential"
            for h in (c.get("code_refs") or []):
                key = (h, sid)
                cur = hash_section_best_rel.get(key)
                if cur is None or _RELEVANCE_RANK.get(rel, 9) < _RELEVANCE_RANK.get(cur, 9):
                    hash_section_best_rel[key] = rel

    # Pass 2: group by hash; only hashes claimed by ≥2 distinct sections
    # need deduplication.
    hash_section_options: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (h, sid), rel in hash_section_best_rel.items():
        hash_section_options[h].append((sid, rel))

    # Snapshot pool sizes for tie-breaking (use original sizes; don't
    # update during the loop — order-dependent tie-break would make
    # behavior non-deterministic across iterations).
    section_pool_sizes: dict[str, int] = {
        sid: sum(len(c.get("code_refs") or []) for c in contribs)
        for sid, contribs in per_section_index.items()
    }

    n_hashes_deduped = 0
    n_refs_removed = 0
    for h, options in hash_section_options.items():
        if len(options) <= 1:
            continue
        n_hashes_deduped += 1
        # Pick: strongest relevance, then smallest pool, then sorted sid
        # (final tiebreak deterministic).
        best_sid = min(options, key=lambda x: (
            _RELEVANCE_RANK.get(x[1], 9),
            section_pool_sizes.get(x[0], 0),
            x[0],
        ))[0]
        # Strip h from every OTHER section's contributions.
        for sid, _rel in options:
            if sid == best_sid:
                continue
            for c in per_section_index[sid]:
                refs = c.get("code_refs") or []
                if h in refs:
                    c["code_refs"] = [r for r in refs if r != h]
                    n_refs_removed += 1
    return n_hashes_deduped, n_refs_removed

def _placeholder_section(
    *,
    section_id: str,
    heading: str,
    n_repairs: int,
    deployment_writer: Optional[str],
) -> Section:
    """Returned when every writer draft + every repair attempt fails.
    Keeps the chapter assemblable and surfaces the failure to
    mgsr_replan via `issues`.

    v2 cookbook schema: empty subtopics list signals "no code emitted";
    the checklist density gate flags this for the mgsr→sawc loop.
    """
    return Section(
        section_id=section_id,
        heading=heading,
        intro=(
            f"This section ({heading}) is awaiting content. The synth "
            f"writer was unable to produce a valid draft on its initial "
            f"pass; mgsr_replan should retarget this section or merge "
            f"it into an adjacent section in the next iteration."
        ),
        subtopics=[],
        citations=[],
        n_drafts_tried=_N_DRAFTS,
        n_repairs=n_repairs,
        deployment_writer=deployment_writer,
        issues=["placeholder"],
    )

async def _write_section_best_of_n(
    *,
    sem: asyncio.Semaphore,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    vault_rich: dict | None = None,
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    thread_id: str,
    prose_mode: bool = False,
    already_shown_hashes: set[str] | None = None,
) -> Section:
    """Full per-section pipeline: N drafts → critic-pick → Section.

    DD-SYNTH-SPEED-SOTA #4 (2026-05-26) — Optimal-Stopping BoN: fire draft 1
    sequentially; if it passes the deterministic "good enough" gate (zero
    violations + >=N_min subtopics + >=N_min citations), ship it directly
    and skip the remaining N-1 drafts. Otherwise fall through to the
    original parallel fan-out + pairwise tournament. arXiv 2510.01394
    (Oct 2025): 15-35% sample reduction at equal Best-of-N quality.
    Disabled via `KD_SAWC_OPTIMAL_STOPPING=false`.
    """
    async with sem:
        t0 = time.monotonic()

        def _make_draft_coro(idx: int):
            return _draft_one_section(
                draft_idx=idx,
                n_total=_N_DRAFTS,
                thread_id=thread_id,
                framework=framework,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                section_id=section_id,
                section_heading=section_heading,
                section_description=section_description,
                section_prerequisites=section_prerequisites,
                contributions=contributions,
                allowed_hashes=allowed_hashes,
                valid_source_keys=valid_source_keys,
                memory=memory,
                n_primary_contribs=n_primary_contribs,
                vault_rich=vault_rich,
                prose_mode=prose_mode,
                already_shown_hashes=already_shown_hashes,
            )

        if _OPTIMAL_STOPPING_ENABLED and _N_DRAFTS >= 2:
            # Fire draft 1 first, decide whether to fire the rest
            r0 = await _make_draft_coro(0)
            results = [r0]
            draft1, _dep1, _wall1, _repairs1 = r0
            good_enough = False
            if draft1 is not None:
                issues_1 = validate_section_against_inputs(
                    draft1,
                    expected_heading=section_heading,
                    allowed_hashes=set(allowed_hashes),
                    valid_source_keys=set(valid_source_keys),
                    vault_rich=vault_rich,
                )
                if (
                    len(issues_1) == 0
                    and len(draft1.subtopics) >= _OPTIMAL_STOPPING_MIN_SUBTOPICS
                    and len(draft1.citations) >= _OPTIMAL_STOPPING_MIN_CITATIONS
                ):
                    good_enough = True
            if not good_enough:
                # Fan out remaining drafts in parallel
                remaining = await asyncio.gather(*[
                    _make_draft_coro(i) for i in range(1, _N_DRAFTS)
                ])
                results.extend(remaining)
        else:
            # Original parallel fan-out (kill switch or N=1)
            results = await asyncio.gather(*[
                _make_draft_coro(i) for i in range(_N_DRAFTS)
            ])

        # Filter to drafts that parsed + validated
        valid: list[tuple[int, _LLMSectionDraft, str, int, int]] = []
        for i, (draft, dep, wall, repairs) in enumerate(results):
            if draft is not None:
                valid.append((i, draft, dep or "", wall, repairs))

        if not valid:
            # ALL drafts failed → placeholder
            await emit_progress(
                thread_id, "sawc_write", "section_picked",
                section_id=section_id, chosen_idx=-1,
                n_violations=0, fallback="all_drafts_failed",
                structural_score=0.0,
            )
            await emit_progress(
                thread_id, "sawc_write", "section_done",
                section_id=section_id, n_subtopics=0,
                n_citations=0, total_explanation_chars=0,
                n_repairs=sum(r[3] for r in results),
                wall_ms=int((time.monotonic() - t0) * 1000),
                fallback="placeholder",
            )
            return _placeholder_section(
                section_id=section_id,
                heading=section_heading,
                n_repairs=sum(r[3] for r in results),
                deployment_writer=(
                    next((d for _, _, d, _, _ in valid), None)
                    if valid else None
                ),
            )

        # Critic picker over valid drafts (rerank, not regenerate)
        chosen_idx, dep_critic, fallback, structural_score = (
            await _critic_pick_best(
                section_id=section_id,
                section_heading=section_heading,
                n_primary_contribs=n_primary_contribs,
                candidates=[d for _, d, _, _, _ in valid],
                expected_heading=section_heading,
                allowed_hashes=set(allowed_hashes),
                valid_source_keys=set(valid_source_keys),
                thread_id=thread_id,
                vault_rich=vault_rich,
            )
        )

        # Map picker index → original draft index (for transparency)
        original_draft_idx = valid[chosen_idx][0]
        chosen_draft = valid[chosen_idx][1]
        dep_writer = valid[chosen_idx][2]
        chosen_repairs = valid[chosen_idx][4]

        # Re-validate the chosen draft so `issues` is accurate (in case
        # the picker chose one with remaining violations after repair
        # exhaustion)
        chosen_issues = validate_section_against_inputs(
            chosen_draft,
            expected_heading=section_heading,
            allowed_hashes=set(allowed_hashes),
            valid_source_keys=set(valid_source_keys),
            vault_rich=vault_rich,
        )

        await emit_progress(
            thread_id, "sawc_write", "section_picked",
            section_id=section_id,
            chosen_idx=original_draft_idx,
            n_violations=len(chosen_issues),
            fallback=fallback,
            structural_score=structural_score,
            deployment_critic=dep_critic,
        )

        section = Section(
            section_id=section_id,
            heading=chosen_draft.heading,
            intro=chosen_draft.intro,
            subtopics=chosen_draft.subtopics,
            citations=chosen_draft.citations,
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment_writer=dep_writer,
            deployment_critic=dep_critic,
            n_drafts_tried=_N_DRAFTS,
            n_repairs=chosen_repairs,
            chosen_draft_idx=original_draft_idx,
            structural_score=structural_score,
            fallback_picker=fallback,
            issues=chosen_issues,
        )

        total_expl_chars = sum(
            len(st.explanation) for st in section.subtopics
        )
        await emit_progress(
            thread_id, "sawc_write", "section_done",
            section_id=section_id,
            n_subtopics=len(section.subtopics),
            n_citations=len(section.citations),
            total_explanation_chars=total_expl_chars,
            n_repairs=chosen_repairs,
            wall_ms=section.wall_ms,
        )
        return section

def _compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    digest_manifest_hash: str,
    refine_iter: int = 0,
) -> str:
    """Content-addressed manifest hash for sawc cache key. Includes
    refine_iter (2026-05-24, CoRefine loop closure) so each mgsr→sawc loop
    iteration produces fresh drafts via bandit-routed exploration — without
    this, the cache would short-circuit the loop with stale results."""
    payload = (
        f"outline={outline_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={SAWC_PROMPT_VERSION}|"
        f"schema={SAWC_SCHEMA_VERSION}|"
        f"iter={refine_iter}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# === sawc round 2 helpers from old commit ===
_OPTIMAL_STOPPING_MIN_SUBTOPICS = 4

_OPTIMAL_STOPPING_MIN_CITATIONS = 2

_OPTIMAL_STOPPING_ENABLED = os.environ.get(
    "KD_SAWC_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

_RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}

async def _draft_one_section(
    *,
    draft_idx: int,
    n_total: int,
    thread_id: str,
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
) -> tuple[Optional[_LLMSectionDraft], Optional[str], int, int]:
    """One writer call → parse → Pydantic → cross-ref → repair.

    Returns (draft, deployment, wall_ms, n_repairs). draft is None
    on irrecoverable failure.

    Emits ONE `section_draft_done` event so the UI shows progress
    through the N=3 fan-out (real-time mechanism we established for
    outline_sdp + digest_construct)."""
    t0 = time.monotonic()
    allowed_hash_set = set(allowed_hashes)
    valid_source_set = set(valid_source_keys)

    prompt = build_writer_prompt(
        framework=framework,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        section_id=section_id,
        section_heading=section_heading,
        section_description=section_description,
        section_prerequisites=section_prerequisites,
        contributions=contributions,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        memory=memory,
        n_primary_contribs=n_primary_contribs,
        vault_rich=vault_rich,
        prose_mode=prose_mode,
        already_shown_hashes=already_shown_hashes,
    )

    deployment: Optional[str] = None
    try:
        # Option B (2026-05-24): writer drafts use the dd-synth-write
        # bandit pool restricted to heavyweight reasoning models.
        # Workhorse arms (mistral-small, magistral-small, devstral-medium
        # under medium budget) stay reserved for dd-grader filter tasks.
        # DD-SYNTH-SPEED-SOTA #1 (2026-05-26): response_format=json_schema
        # is attached server-side for NIM/Mistral arms — repair loop below
        # still handles Gemini and any provider slip-through.
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_DRAFT,
            temperature=_TEMPERATURE_DRAFT,
            dd_process="dd-synth-write",
            candidate_filter=_sawc_writer_filter,
            response_format=_SAWC_DRAFT_RESPONSE_FORMAT,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"{type(e).__name__}: {str(e)[:120]}",
            wall_ms=wall_ms,
        )
        return None, None, wall_ms, 0

    parsed = _parse_json_response(response)
    if not parsed:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error="parse_failed", wall_ms=wall_ms,
            deployment=deployment,
        )
        return None, deployment, wall_ms, 0

    draft, err = _try_parse_draft(parsed)
    n_repairs = 0
    current = parsed

    # Pydantic-fail repair loop
    while draft is None and n_repairs < _MAX_REPAIR_ATTEMPTS:
        n_repairs += 1
        issues = [f"Pydantic schema rejected the previous output: {err}"]
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            section_id=section_id,
            section_heading=section_heading,
            section_description=section_description,
            section_prerequisites=section_prerequisites,
            contributions=contributions,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            memory=memory,
            current_json=json.dumps(current, indent=2),
            issues=issues,
            prose_mode=prose_mode,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp:
                current = rp
                draft, err = _try_parse_draft(rp)
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: repair "
                f"attempt {n_repairs} failed: {type(e).__name__}: {e}"
            )
            break

    if draft is None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"pydantic_fail: {err}",
            wall_ms=wall_ms, deployment=deployment,
        )
        return None, deployment, wall_ms, n_repairs

    # Cross-ref validation (heading/hashes/citations + Ship B/E alignment)
    issues = validate_section_against_inputs(
        draft,
        expected_heading=section_heading,
        allowed_hashes=allowed_hash_set,
        valid_source_keys=valid_source_set,
        vault_rich=vault_rich,
    )
    # S3 (2026-05-26 late evening) — repair only on HARD issues. Soft
    # quality-nudge issues (subheading/explanation↔code mismatch,
    # subtopic-shy-of-bank) still ship in .issues for downstream but
    # don't burn the repair budget — the LLM can't reliably close them.
    while hard_issues(issues) and n_repairs < _MAX_REPAIR_ATTEMPTS:
        n_repairs += 1
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            section_id=section_id,
            section_heading=section_heading,
            section_description=section_description,
            section_prerequisites=section_prerequisites,
            contributions=contributions,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            memory=memory,
            current_json=json.dumps(draft.model_dump(), indent=2),
            issues=issues,
            prose_mode=prose_mode,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if not rp:
                break
            new_draft, new_err = _try_parse_draft(rp)
            if new_draft is None:
                break
            new_issues = validate_section_against_inputs(
                new_draft,
                expected_heading=section_heading,
                allowed_hashes=allowed_hash_set,
                valid_source_keys=valid_source_set,
                vault_rich=vault_rich,
            )
            # Accept ONLY if it strictly reduces violation count
            # S3 — accept only when HARD issues strictly decreased.
            if len(hard_issues(new_issues)) < len(hard_issues(issues)):
                draft = new_draft
                issues = new_issues
            else:
                break
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: cross-ref "
                f"repair attempt {n_repairs} failed: "
                f"{type(e).__name__}: {e}"
            )
            break

    wall_ms = int((time.monotonic() - t0) * 1000)
    await emit_progress(
        thread_id, "sawc_write", "section_draft_done",
        section_id=section_id, draft_idx=draft_idx, n_total=n_total,
        ok=True, wall_ms=wall_ms, deployment=deployment,
        n_subtopics=len(draft.subtopics),
        n_citations=len(draft.citations),
        n_violations=len(issues),
    )
    return draft, deployment, wall_ms, n_repairs

async def _critic_pick_best(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    candidates: list[_LLMSectionDraft],
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    thread_id: str,
    vault_rich: dict | None = None,
) -> tuple[int, Optional[str], Optional[str], float]:
    """Pairwise tournament picker. Returns
    (chosen_idx, deployment_critic, fallback_used, structural_score).

    fallback_used ∈ {None, "structural_score"} — None means at least one
    pairwise match got a clean LLM verdict; "structural_score" means every
    match fell back to deterministic tiebreak.

    For N=3: 2 matches (knockout). For N=2: 1 match. For N=1: trivial.
    """
    summaries = [
        summarize_candidate(
            c,
            expected_heading=expected_heading,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            n_primary_contribs=n_primary_contribs,
            vault_rich=vault_rich,
        )
        for c in candidates
    ]

    if len(candidates) <= 1:
        score = summaries[0]["structural_score"] if summaries else 0.0
        return 0, None, None, score

    # Knockout: indices represent positions in `candidates`. Each match
    # picks between two positions; the winner advances.
    n = len(candidates)
    advancing = list(range(n))
    deployment_critic: Optional[str] = None
    n_llm_picks = 0

    # Pairwise knockout — log_2(N) rounds, but for N=3 it's just 2 matches:
    # round 1: cand[0] vs cand[1]; round 2: winner vs cand[2].
    while len(advancing) > 1:
        next_round: list[int] = []
        # Pair the front: idx_a vs idx_b → winner. Carry odd survivor forward.
        i = 0
        while i + 1 < len(advancing):
            idx_a, idx_b = advancing[i], advancing[i + 1]
            winner_letter, dep = await _pairwise_judge_match(
                section_id=section_id,
                section_heading=section_heading,
                n_primary_contribs=n_primary_contribs,
                summary_a=summaries[idx_a],
                summary_b=summaries[idx_b],
            )
            if dep is not None:
                deployment_critic = dep
                n_llm_picks += 1
            next_round.append(idx_a if winner_letter == "A" else idx_b)
            i += 2
        if i < len(advancing):
            next_round.append(advancing[i])  # bye for odd survivor
        advancing = next_round

    winner_idx = advancing[0]
    fallback_used = None if n_llm_picks > 0 else "structural_score"
    return (
        winner_idx,
        deployment_critic,
        fallback_used,
        summaries[winner_idx]["structural_score"],
    )


# === sawc round 3 helpers ===
_TEMPERATURE_DRAFT     = 0.5

_TEMPERATURE_REPAIR    = 0.2

_MAX_TOKENS_DRAFT      = 8000

_MAX_TOKENS_REPAIR     = 8000

_MAX_REPAIR_ATTEMPTS   = 2

_SAWC_DRAFT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "section_draft",
        "schema": _LLMSectionDraft.model_json_schema(),
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

def _try_parse_draft(
    raw: dict,
) -> tuple[Optional[_LLMSectionDraft], Optional[str]]:
    try:
        return _LLMSectionDraft.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"

async def _pairwise_judge_match(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    summary_a: dict,
    summary_b: dict,
) -> tuple[str, Optional[str]]:
    """Run ONE pairwise match. Returns (winner_letter, deployment_critic).

    winner_letter ∈ {"A", "B"}. On any parse / call failure, returns the
    structural-score winner via deterministic tiebreak — the tournament
    never aborts.
    """
    # Compact JSON-stringified summary keeps the prompt token-light.
    def _fmt_summary(s: dict) -> str:
        return json.dumps(
            {
                "structural_score": s.get("structural_score"),
                "n_paragraphs":     s.get("n_paragraphs"),
                "total_chars":      s.get("total_chars"),
                "n_code_refs":      s.get("n_code_refs"),
                "n_citations":      s.get("n_citations"),
                "heading_matches":  s.get("heading_matches"),
                "n_unknown_hashes": s.get("n_unknown_hashes"),
                "n_unknown_keys":   s.get("n_unknown_keys"),
            },
            indent=2,
        )

    prompt = _PAIRWISE_PICKER_PROMPT.format(
        section_heading=section_heading,
        n_primary_contribs=n_primary_contribs,
        summary_a=_fmt_summary(summary_a),
        summary_b=_fmt_summary(summary_b),
    )

    try:
        # DD-SYNTH-SPEED-SOTA #A7 (2026-05-26): json_object forces the
        # pairwise critic to emit valid JSON {"winner": "A"|"B", "reason": ...}
        # without prose preamble, eliminating ~most parse-failed tiebreaks.
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_CRITIC,
            temperature=_TEMPERATURE_CRITIC,
            response_format={"type": "json_object"},
        )
        deployment_critic = (meta or {}).get("deployment")
        parsed = _parse_json_response(response)
        if parsed and "winner" in parsed:
            w = str(parsed["winner"]).strip().upper()[:1]
            if w in ("A", "B"):
                return w, deployment_critic
    except Exception as e:
        logger.warning(
            f"[sawc_write] {section_id}: pairwise match failed: "
            f"{type(e).__name__}: {e} — structural tiebreak"
        )

    # Structural tiebreak — never abort the tournament.
    s_a = summary_a.get("structural_score", 0.0)
    s_b = summary_b.get("structural_score", 0.0)
    return ("A" if s_a >= s_b else "B"), None


# === sawc round 4 helpers ===
# --- _JSON_RE ---
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# --- _MAX_TOKENS_CRITIC ---
_MAX_TOKENS_CRITIC     = 300

# --- _PAIRWISE_PICKER_PROMPT ---
_PAIRWISE_PICKER_PROMPT = """You are picking the BETTER of two technical-documentation
drafts for the same section. The section is part of a larger distilled book.

Choose by these criteria in order:
1. Checklist coverage (does the draft address every outline point named?)
2. Citation density (does it cite/reference the source documentation it claims?)
3. Structural completeness (no truncations, no orphan code-refs, no placeholder text)
4. Clarity and concision (well-organized, no rambling)

You MUST choose A or B. Ties are NOT allowed.

=== SECTION ===
heading: {section_heading}
expected primary source contributions: {n_primary_contribs}

=== DRAFT A — structural summary ===
{summary_a}

=== DRAFT B — structural summary ===
{summary_b}

Answer in JSON: {{"winner": "A" | "B", "reason": "one short sentence"}}"""

# --- _TEMPERATURE_CRITIC ---
_TEMPERATURE_CRITIC    = 0.0

# --- _shorten_pydantic_error ---
def _shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


async def sawc_write_run(state: SynthState) -> dict:
    """Run the Section-Aware Writer-Critic for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "sawc_path":  "",
            "sawc_stats": {"skipped": "no_slug_or_chapter_id", "wall_ms": 0},
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    outline_key = _outline_latest_key(slug, chapter_id)
    digest_key = _digest_latest_key(slug, chapter_id)

    if not await minio.exists(outline_key):
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":     "outline_not_found",
                "outline_key": outline_key,
                "wall_ms":     int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline {outline_key!r} not in MinIO — run outline_sdp first",
        }
    if not await minio.exists(digest_key):
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":    "digest_not_found",
                "digest_key": digest_key,
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"digest {digest_key!r} not in MinIO — run digest_construct first",
        }

    try:
        outline_text = await minio.read_text(outline_key)
        outline_payload = json.loads(outline_text)
        digest_text = await minio.read_text(digest_key)
        digest_payload = json.loads(digest_text)
    except Exception as e:
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline/digest unreadable: {type(e).__name__}: {e}",
        }

    outline_data = outline_payload.get("outline") or {}
    outline_sections = outline_data.get("sections") or []
    dag = outline_payload.get("dag") or {}
    stages_raw = dag.get("stages") or {}
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""

    per_section_index: dict[str, list[dict]] = (
        digest_payload.get("per_section") or {}
    )
    # Cross-section vault-hash uniqueness — digest allows the same hash
    # for different sections; without dedup CLI corpora recycle 3-5 H2s.
    n_hashes_deduped, n_refs_removed = _dedupe_vault_hashes_across_sections(
        per_section_index,
    )
    if n_hashes_deduped:
        logger.info(
            f"[sawc_write] {slug}/{chapter_id}: cross-section dedup — "
            f"{n_hashes_deduped} hashes claimed by multiple sections; "
            f"removed {n_refs_removed} duplicate code_ref entries"
        )
    per_source_list: list[dict] = digest_payload.get("per_source") or []
    valid_source_keys: list[str] = sorted({
        s.get("source_key", "") for s in per_source_list
        if s.get("source_key")
    })
    digest_manifest_hash = digest_payload.get("digest_manifest_hash") or ""

    if not outline_sections or not stages_raw:
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":    "empty_outline_or_stages",
                "n_sections": len(outline_sections),
                "n_stages":   len(stages_raw),
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline has {len(outline_sections)} sections, dag "
                      f"has {len(stages_raw)} stages — both must be >0",
        }

    # Build section_id → outline_section lookup
    sections_by_id: dict[str, dict] = {
        s["section_id"]: s for s in outline_sections
    }
    # Normalize stage keys to int and sort
    stages: dict[int, list[str]] = {
        int(k): list(v) for k, v in stages_raw.items()
    }
    # Skip sections that digest source-pool merge folded elsewhere —
    # contributions are re-tagged to the winner; writing them produces a
    # hollow shell that bank-padding then back-fills with canonical code.
    merged_away: set[str] = set(
        (digest_payload.get("merged_sections") or {}).keys()
    )
    if merged_away:
        stages = {
            k: [sid for sid in v if sid not in merged_away]
            for k, v in stages.items()
        }
        stages = {k: v for k, v in stages.items() if v}
        logger.info(
            f"[sawc_write] {slug}/{chapter_id}: skipping "
            f"{len(merged_away)} digest-merged section(s) "
            f"{sorted(merged_away)}"
        )
    sorted_stage_indices = sorted(stages.keys())
    n_sections = sum(len(v) for v in stages.values())
    n_stages = len(sorted_stage_indices)

    await emit_progress(
        thread_id, "sawc_write", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_stages = n_stages,
        n_sections = n_sections,
        n_total_drafts = n_sections * N_DRAFTS,
    )

    # Track the iteration counter for the CoRefine loop (2026-05-24).
    # Each sawc_write invocation bumps it by 1; refine_iter is part of the
    # manifest hash so loop iterations don't cache-hit each other.
    incoming_refine_iter = int(state.get("refine_iter") or 0)
    refine_iter = incoming_refine_iter + 1

    # Best-seen iteration tracking — checklist score updated in mgsr_replan
    # after sawc returns; render falls back to this at budget halt.
    incoming_best_score = state.get("best_seen_score")
    incoming_best_path = state.get("best_seen_sawc_path")
    incoming_prev_score = state.get("prev_checklist_score")

    manifest_hash = _compute_manifest_hash(
        outline_manifest_hash = outline_manifest_hash,
        digest_manifest_hash = digest_manifest_hash,
        refine_iter = refine_iter,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            cov = (cached or {}).get("coverage_stats") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sections":      cov.get("n_sections", 0),
                "n_completed":     cov.get("n_sections_completed", 0),
                "n_fallback":      cov.get("n_sections_fallback", 0),
                "n_repairs":       cov.get("n_repairs", 0),
                "n_stages":        cov.get("n_stages", 0),
                "n_total_drafts_fired": cov.get("n_total_drafts_fired", 0),
                "n_picker_fallbacks":   cov.get("n_picker_fallbacks", 0),
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "sawc_write", "done",
                n_sections = stats["n_sections"],
                n_completed = stats["n_completed"],
                n_fallback = stats["n_fallback"],
                n_repairs = stats["n_repairs"],
                total_drafts_fired = stats["n_total_drafts_fired"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(
                f"[sawc_write] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_completed']}/{stats['n_sections']} sections, "
                f"{stats['n_repairs']} repairs, {elapsed} ms"
            )
            # Cache hit preserves best-seen — same draft, unchanged tracking.
            patch = {
                "sawc_path":   latest_key,
                "sawc_stats":  stats,
                "refine_iter": refine_iter,
            }
            if incoming_best_path:
                patch["best_seen_sawc_path"] = incoming_best_path
            if incoming_best_score is not None:
                patch["best_seen_score"] = incoming_best_score
            return patch
        except Exception as e:
            logger.warning(
                f"[sawc_write] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # Load the full vault entries for every source contributing to this
    # chapter so the writer prompt can render <code id = "..." lang = "...">
    # {body}</code> envelopes — the LLM sees actual code instead of opaque
    # hashes. Render-time substitution still uses the same hash → vault[id]
    # path so byte-perfect fidelity is preserved (Deterministic Quoting,
    # Yeung 2025; arXiv 2601.03640).
    vault_rich, n_vaults_loaded, n_vaults_skipped = await _load_chapter_vault_rich(
        minio, slug, valid_source_keys,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: visible vault loaded — "
        f"{len(vault_rich)} entries across {n_vaults_loaded} sources "
        f"(skipped {n_vaults_skipped})"
    )

    sem = asyncio.Semaphore(_CONCURRENCY)
    memory_ledger: list[MemoryEntry] = []
    completed_sections: dict[str, Section] = {}
    # Fix #3 — cross-section recycling: code_ref_hashes already rendered as
    # subtopics by COMPLETED (prior-stage) sections of this chapter. Passed to
    # later sections' writer prompts so they reference rather than re-show.
    chapter_used_hashes: set[str] = set()
    n_total_drafts_fired = 0
    n_critic_picks = 0
    n_picker_fallbacks = 0

    for stage_idx in sorted_stage_indices:
        stage_section_ids = stages[stage_idx]
        stage_t0 = time.monotonic()
        await emit_progress(
            thread_id, "sawc_write", "stage_start",
            stage_idx = stage_idx,
            n_sections_in_stage = len(stage_section_ids),
            section_ids = stage_section_ids,
        )

        # Freeze memory snapshot for this stage — all sections at this
        # stage see the SAME memory (per SurveyGen-I §3.2.2: memory
        # accumulates BETWEEN stages, not within)
        memory_snapshot = [m.model_dump() for m in memory_ledger]

        async def _run_section(sid: str) -> Section:
            outline_sec = sections_by_id.get(sid)
            if not outline_sec:
                logger.warning(
                    f"[sawc_write] section_id {sid!r} in stages but not in "
                    f"outline.sections — emitting placeholder"
                )
                return _placeholder_section(
                    section_id = sid,
                    heading = sid,
                    n_repairs = 0,
                    deployment_writer = None,
                )
            contributions = per_section_index.get(sid) or []
            # Allowed hashes = union of code_refs across contributions
            # (digest-routed). Padded below from chapter-wide vault when
            # under-routing leaves the bank thin.
            allowed_hashes_set: set[str] = set()
            for c in contributions:
                for h in (c.get("code_refs") or []):
                    allowed_hashes_set.add(h)
            # Gate prose_mode on PRE-pad count — post-pad would pull stray
            # hashes into a no-code section and emit empty placeholders.
            n_routed_hashes = len(allowed_hashes_set)
            # Pad thin banks (<6 hashes) with up to 20 pedagogically-ranked
            # chapter-wide hashes; LLM picks 3-6 via visible-vault renderer.
            _MIN_BANK_SIZE = 6
            _BANK_PAD_TO = 20
            if vault_rich and len(allowed_hashes_set) < _MIN_BANK_SIZE:
                chapter_wide = list(vault_rich.keys())
                ranked_chapter = _rank_hashes_by_pedagogy(
                    chapter_wide, vault_rich,
                )
                needed = _BANK_PAD_TO - len(allowed_hashes_set)
                pads = [
                    h for h in ranked_chapter
                    if h not in allowed_hashes_set
                ][:needed]
                if pads:
                    allowed_hashes_set.update(pads)
                    logger.info(
                        f"[sawc_write] {sid}: digest-routed bank had "
                        f"{len(allowed_hashes_set) - len(pads)} hashes < "
                        f"{_MIN_BANK_SIZE}; padded with {len(pads)} pedagogically-"
                        f"ranked chapter-wide hashes → bank size now "
                        f"{len(allowed_hashes_set)}"
                    )

            # Re-order by pedagogical score (canonical small examples
            # first); fall back to sorted-hash if vault is empty.
            if vault_rich:
                allowed_hashes = _rank_hashes_by_pedagogy(
                    sorted(allowed_hashes_set), vault_rich,
                )
            else:
                allowed_hashes = sorted(allowed_hashes_set)
            n_primary_contribs = sum(
                1 for c in contributions if c.get("relevance") == "primary"
            )
            # U7 (2026-05-28) — per-section source-doc binding. Restrict
            # citations to source docs that digest_construct actually
            # routed to THIS section, NOT chapter-wide. Combined with
            # U2 vault-hash dedup, this prevents the writer from citing
            # sources that "belong to" other sections — closing the
            # belt-and-suspenders loop on cross-section drift.
            #
            # Fail-safe: if a section ends up with zero contributing
            # sources (digest under-routed), fall back to chapter-wide
            # so the writer still has SOMETHING to cite. Empirically
            # rare but possible on small corpora.
            section_source_keys: list[str] = sorted({
                c.get("source_key", "") for c in contributions
                if c.get("source_key")
            })
            if not section_source_keys:
                section_source_keys = valid_source_keys
                logger.info(
                    f"[sawc_write] {sid}: digest routed 0 sources to "
                    f"this section; falling back to chapter-wide "
                    f"({len(valid_source_keys)} sources) for citations"
                )
            # PROSE PATH (Fix #1, trigger corrected 2026-05-30): a section is
            # conceptual/prose when the digest routed it NO real code
            # (n_routed_hashes == 0), OR when even after Ship-A padding the
            # bank can't sustain the minimum code subtopics (tiny no-code
            # chapter — nothing to pad from). Gating on n_routed (not the
            # padded bank) means a no-code section in a chapter that has a few
            # stray hashes still goes prose instead of failing to a
            # placeholder. Code-rich chapters are unaffected: a section with
            # ≥1 routed hash and a paddable bank stays code-first.
            prose_mode = (n_routed_hashes == 0) or (len(allowed_hashes) < SUBTOPICS_MIN)
            return await _write_section_best_of_n(
                sem = sem,
                section_id = sid,
                section_heading = outline_sec.get("heading") or sid,
                section_description = outline_sec.get("description") or "",
                section_prerequisites = (
                    outline_sec.get("prerequisites") or []
                ),
                contributions = contributions,
                allowed_hashes = allowed_hashes,
                vault_rich = vault_rich,
                valid_source_keys = section_source_keys,
                memory = memory_snapshot,
                n_primary_contribs = n_primary_contribs,
                framework = slug,
                chapter_id = chapter_id,
                chapter_title = chapter_title,
                thread_id = thread_id,
                prose_mode = prose_mode,
                already_shown_hashes = set(chapter_used_hashes),
            )

        section_results = await asyncio.gather(
            *(_run_section(sid) for sid in stage_section_ids),
            return_exceptions = True,
        )

        n_stage_completed = 0
        n_stage_failed = 0
        for sid, result in zip(stage_section_ids, section_results):
            if isinstance(result, BaseException):
                logger.warning(
                    f"[sawc_write] {sid}: gather raised "
                    f"{type(result).__name__}: {result} — emitting placeholder"
                )
                completed_sections[sid] = _placeholder_section(
                    section_id = sid,
                    heading = sections_by_id.get(sid, {}).get("heading", sid),
                    n_repairs = 0,
                    deployment_writer = None,
                )
                n_stage_failed += 1
            else:
                completed_sections[sid] = result
                # All non-placeholder sections count toward drafts fired
                n_total_drafts_fired += N_DRAFTS
                n_critic_picks += 1
                if result.fallback_picker == "structural_score":
                    n_picker_fallbacks += 1
                if "placeholder" in result.issues:
                    n_stage_failed += 1
                else:
                    n_stage_completed += 1

            # Accumulate memory entry deterministically
            sec = completed_sections[sid]
            contribs = per_section_index.get(sid) or []
            try:
                memory_ledger.append(extract_memory_entry(
                    sec,
                    section_contributions = contribs,
                    section_heading = sec.heading,
                ))
            except Exception as e:
                logger.warning(
                    f"[sawc_write] memory extract failed for {sid}: "
                    f"{type(e).__name__}: {e}"
                )

            # Fix #3 — record this section's rendered code so later stages'
            # sections reference rather than re-emit it (anti-recycling).
            for st in (getattr(sec, "subtopics", None) or []):
                h = getattr(st, "code_ref_hash", "")
                if h:
                    chapter_used_hashes.add(h)

        stage_ms = int((time.monotonic() - stage_t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "stage_done",
            stage_idx = stage_idx,
            n_completed = n_stage_completed,
            n_failed = n_stage_failed,
            wall_ms = stage_ms,
        )

    # Preserve outline order so downstream consumers can iterate sections
    # in reading order (sawc returns stage-grouped order; flatten back)
    section_order = [s["section_id"] for s in outline_sections]
    final_sections = [
        completed_sections[sid] for sid in section_order
        if sid in completed_sections
    ]

    coverage = compute_sawc_stats(
        sections = final_sections,
        n_stages = n_stages,
        n_total_drafts_fired = n_total_drafts_fired,
        n_critic_picks = n_critic_picks,
        n_picker_fallbacks = n_picker_fallbacks,
    )

    chapter_draft = ChapterDraft(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        sections = final_sections,
        memory_final = memory_ledger,
        coverage_stats = coverage,
    )

    payload = chapter_draft.model_dump()
    payload["outline_manifest_hash"] = outline_manifest_hash
    payload["digest_manifest_hash"]  = digest_manifest_hash
    payload["sawc_manifest_hash"]    = manifest_hash

    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":            coverage.n_sections,
        "n_completed":           coverage.n_sections_completed,
        "n_fallback":            coverage.n_sections_fallback,
        "n_stages":              coverage.n_stages,
        "n_total_drafts_fired":  coverage.n_total_drafts_fired,
        "n_critic_picks":        coverage.n_critic_picks,
        "n_picker_fallbacks":    coverage.n_picker_fallbacks,
        "n_repairs":             coverage.n_repairs,
        "total_subtopics":       coverage.total_subtopics,
        "total_citations":       coverage.total_citations,
        "avg_subtopics_per_section": coverage.avg_subtopics_per_section,
        "avg_explanation_words":     coverage.avg_explanation_words,
        "wall_ms":               elapsed,
        "store_path":            latest_key,
        "versioned_path":        versioned_key,
        "manifest_hash":         manifest_hash,
        "cache_hit":             False,
        "prompt_version":        SAWC_PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "sawc_write", "done",
        n_sections = stats["n_sections"],
        n_completed = stats["n_completed"],
        n_fallback = stats["n_fallback"],
        n_repairs = stats["n_repairs"],
        total_drafts_fired = stats["n_total_drafts_fired"],
        wall_ms = elapsed,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: "
        f"{stats['n_completed']}/{stats['n_sections']} sections written, "
        f"{stats['n_fallback']} fallbacks, {stats['n_repairs']} repairs, "
        f"{stats['n_total_drafts_fired']} drafts fired, "
        f"{stats['n_picker_fallbacks']} picker fallbacks, "
        f"refine_iter = {refine_iter}, {elapsed} ms"
    )
    # mgsr_replan updates best-seen with the checklist score; here we
    # just forward + default to the current versioned key on first iter.
    patch = {
        "sawc_path":   latest_key,
        "sawc_stats":  stats,
        "refine_iter": refine_iter,
    }
    if incoming_best_path:
        patch["best_seen_sawc_path"] = incoming_best_path
    else:
        # First iteration — current sawc IS the best-seen. We track the
        # VERSIONED key (immutable) not the latest pointer, so render can
        # load this specific iteration even after subsequent iterations
        # overwrite latest_key.
        patch["best_seen_sawc_path"] = versioned_key
    if incoming_best_score is not None:
        patch["best_seen_score"] = incoming_best_score
    return patch


# Convenience loader for downstream nodes
def load_sawc_payload(text: str) -> dict:
    """Parse the persisted sawc blob. Returns the full payload dict;
    downstream nodes pick the fields they need (sections, memory_final,
    coverage_stats, etc.)."""
    return json.loads(text)
