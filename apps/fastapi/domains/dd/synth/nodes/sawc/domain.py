"""SAWC — pure helpers: identifier/code-alignment extraction, structural
validation + scoring, prompt builders, vault-hash dedup, JSON/Pydantic
parsing, and manifest hashing. No I/O, no LLM calls, no logging — see
service.py for the orchestration (LLM drafting, critic pick, pairwise
judge, MinIO persistence) that calls these."""
from __future__ import annotations
import domains
from . import params, schemas, versions

import ast
import json
import re
from collections import defaultdict
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError


IDENT_STOPWORDS = frozenset({
    "self", "cls", "str", "int", "bool", "list", "dict", "set", "tuple",
    "none", "true", "false", "return", "import", "from", "async", "def",
    "class", "yield", "raise", "with", "for", "while", "else", "elif",
    "try", "except", "finally", "not", "and", "the", "this", "that",
    "data", "key", "val", "value", "result", "item", "items", "args",
    "kwargs", "name", "type", "obj", "object", "func", "function",
    "arg", "params", "ctx", "context", "request", "response", "main",
})


def ast_identifiers(code: str) -> set[str]:
    """Best-effort identifier extraction. Python AST covers ~80%; regex fallback for shell/markdown. Stopwords dropped so scaffolding doesn't inflate alignment scores."""
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
        if len(i) >= 3 and i.lower() not in IDENT_STOPWORDS
    }


def prose_tokens(text: str) -> set[str]:
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
    return {w for w in out if w.lower() not in IDENT_STOPWORDS and len(w) >= 3}


def first_lines_word_set(code: str, n_lines: int = 3) -> set[str]:
    """Lowercased word tokens from first N non-blank code lines — softer fallback for subheading alignment when heading tokens match first-line tokens after stopword removal."""
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
            if wl in IDENT_STOPWORDS:
                continue
            out.add(wl)
        n += 1
        if n >= n_lines:
            break
    return out


def identifier_overlap(prose: str, code: str) -> tuple[set[str], set[str]]:
    """Return (overlap_set, code_idents); case-sensitive (get_access_token ≠ Get_Access_Token)."""
    code_idents = ast_identifiers(code)
    if not code_idents:
        return set(), set()
    prose_set = prose_tokens(prose)
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


def extract_memory_entry(
    section: schemas.Section,
    section_contributions: list[dict],
    section_heading: str,
) -> schemas.MemoryEntry:
    """Build MemoryEntry deterministically (saves N LLM calls/chapter). SurveyGen-I §3.2.2 shape; mgsr_replan can upgrade to LLM-extract if needed."""
    parts: list[str] = []
    if section.intro:
        parts.append(section.intro.strip())
    if section.subtopics:
        parts.append(section.subtopics[0].explanation.strip())
    summary = " ".join(parts).strip()
    if len(summary) > params.MEMORY_SUMMARY_CHARS_MAX:
        summary = summary[: params.MEMORY_SUMMARY_CHARS_MAX - 1].rsplit(" ", 1)[0] + "…"
    if len(summary) < params.MEMORY_SUMMARY_CHARS_MIN:
        # Pad with the heading + a generic phrase so the Pydantic min
        # passes; mgsr_replan will flag thin sections via checklist_eval
        summary = (
            f"{section_heading}: {summary}"
            if summary
            else f"{section_heading}: (no content)"
        )
        if len(summary) < params.MEMORY_SUMMARY_CHARS_MIN:
            summary = summary + " — content pending refinement."

    candidates: list[str] = []
    for contrib in section_contributions or []:
        for fact in (contrib.get("key_facts") or []):
            # Pull `inline_code` spans
            for m in re.finditer(r"`([^`]+)`", fact):
                t = m.group(1).strip()
                if 2 <= len(t) <= params.MEMORY_TERM_CHARS_MAX:
                    candidates.append(t)
            # Pull capitalized identifiers (PascalCase or camelCase)
            for m in re.finditer(r"\b([A-Z][a-zA-Z0-9_]{2,})\b", fact):
                t = m.group(1).strip()
                if 3 <= len(t) <= params.MEMORY_TERM_CHARS_MAX:
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
        if len(terminology) >= params.MEMORY_TERMS_MAX:
            break

    return schemas.MemoryEntry(
        section_id = section.section_id,
        heading = section_heading,
        summary = summary,
        key_terminology = terminology,
    )


SOFT_ISSUE_PREFIXES = (
    "subheading↔code mismatch",
    "explanation↔code mismatch",
    "subtopics has only ",
)


def hard_issues(issues: list[str]) -> list[str]:
    """Filter to issues that trigger writer repair. Soft issues still ship in .issues for visibility but skip repair — writer can't reliably close them."""
    return [
        i for i in issues
        if not any(i.startswith(p) for p in SOFT_ISSUE_PREFIXES)
    ]


def validate_section_against_inputs(
    draft: schemas.LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    vault_rich: dict | None = None,
) -> list[str]:
    """Cross-reference rules beyond Pydantic: heading drift, hallucinated hashes/source_keys, subheading↔code mismatch, explanation↔code mismatch. vault_rich=None skips code-body checks gracefully."""
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
        floor = max(params.SUBTOPICS_MIN, 3)
    else:
        floor = params.SUBTOPICS_MIN
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
            code_idents = ast_identifiers(body)
            if not code_idents:
                # No identifiers extractable (e.g., directory tree or
                # plain markdown) — skip both alignment checks; let the
                # writer's heuristics handle it.
                continue

            # subheading↔code. Strict-AST overlap first; if zero,
            # fall back to first-3-lines word overlap (catches less
            # tightly-named patterns like 'Minimal Tool Definition').
            sub_overlap = prose_tokens(s.subheading) & code_idents
            if not sub_overlap:
                head_words = first_lines_word_set(body, n_lines = 3)
                head_overlap = {
                    w.lower() for w in prose_tokens(s.subheading)
                } & head_words
                if not head_overlap:
                    misaligned_sub.append(s.subheading)

            # explanation↔code: inline `code` span = high-precision; bare words = low-precision. Floor: any backtick match OR ≥2 distinct bare overlaps (1 bare is too easy to game).
            inline_prose = set()
            for tk in re.findall(r"`([^`]+)`", s.explanation):
                for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", tk):
                    if w.lower() not in IDENT_STOPWORDS and len(w) >= 3:
                        inline_prose.add(w)
            inline_match = inline_prose & code_idents
            if inline_match:
                pass  # high-precision signal — accept
            else:
                bare_overlap, _ = identifier_overlap(s.explanation, body)
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


def score_draft_structural(
    draft: schemas.LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> float:
    """Structural quality score (Self-Certainty proxy, arXiv 2502.18581) for when critic LLM fails. Penalizes vault/citation violations and heading mismatch; rewards subtopic count + citation density + explanation length."""
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


def compute_sawc_stats(
    sections: list[schemas.Section],
    n_stages: int,
    n_total_drafts_fired: int,
    n_critic_picks: int,
    n_picker_fallbacks: int,
) -> schemas.SAWCStats:
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
    n_sections_citation_fallback = sum(
        1 for s in sections if "citation_fallback" in s.issues
    )
    n_repairs = sum(s.n_repairs for s in sections)
    total_subtopics = sum(len(s.subtopics) for s in sections)
    total_citations = sum(len(s.citations) for s in sections)
    total_expl_words = sum(
        len((st.explanation or "").split())
        for s in sections for st in s.subtopics
    )
    return schemas.SAWCStats(
        n_sections = n_sections,
        n_sections_completed = n_sections_completed,
        n_sections_fallback = n_sections_fallback,
        n_sections_citation_fallback = n_sections_citation_fallback,
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


def format_contributions_block(contributions: list[dict]) -> str:
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


def format_memory_block(memory: list[dict]) -> str:
    """Pretty-format the memory ledger for the writer prompt; accepts dicts (model_dump() or raw) to avoid callers coercing to MemoryEntry."""
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


MAX_VAULT_CHARS_PER_ENTRY = 6_000


MAX_VAULT_CHARS_TOTAL     = 60_000


_CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same idiom as outline_sdp/digest_construct's
    classifiers. The Rotator is a universal gateway with no context-length
    -aware arm filtering."""
    msg = str(e).lower()
    return any(marker in msg for marker in _CONTEXT_OVERFLOW_MARKERS)


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
    vault_char_budget: int | None = None,
    prior_feedback: list[str] | None = None,
) -> str:
    """Build the per-section writer prompt. vault_rich enables Visible Vault (LLM sees code bodies; hash-only listing otherwise). prose_mode=True when bank is empty (prose subtopics instead of placeholder). already_shown_hashes suppresses cross-section hash recycling. vault_char_budget overrides MAX_VAULT_CHARS_TOTAL — used to retry at a smaller budget after a context-overflow failure. prior_feedback: checklist's failed-criteria feedback strings from the PREVIOUS RETHINK iteration — closes the self-refine loop (arXiv 2303.17651 requires critique to inform regeneration; before this, a RETHINK iteration reran blind with zero signal about what was actually wrong, which is why some iterations regressed instead of improving)."""
    prior_feedback_block = ""
    if prior_feedback:
        feedback_lines = "\n".join(f"  - {fb}" for fb in prior_feedback[:8])
        prior_feedback_block = (
            f"== PRIOR ATTEMPT FEEDBACK — FIX THESE ==\n"
            f"The last draft of this chapter failed review for reasons "
            f"below. This is a REWRITE, not a first draft — address these "
            f"specifically, don't just repeat the same approach:\n"
            f"{feedback_lines}\n\n"
        )
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites
        else "(none — this is a stage-0 section)"
    )
    # A section with an empty code bank is a conceptual/prose topic — write
    # prose subtopics rather than failing to an empty placeholder.
    prose = prose_mode or not allowed_hashes

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

    # Visible vault — LLM sees code bodies (budget-capped: the Rotator is
    # a universal gateway with no context-length-aware arm filtering, so
    # an uncapped bank can exceed a small-context arm from a heterogeneous
    # pool — see format_entries_for_prompt). Render still substitutes via
    # hash so final output is byte-perfect regardless of what got
    # truncated here.
    if allowed_hashes and vault_rich:
        coerced_vault: dict[str, domains.dd.synth.nodes.vault.schemas.VaultEntry] = {}
        for h in allowed_hashes:
            entry = vault_rich.get(h)
            if entry is None:
                continue
            if isinstance(entry, dict):
                try:
                    entry = domains.dd.synth.nodes.vault.schemas.VaultEntry(**entry)
                except Exception:
                    entry = domains.dd.synth.nodes.vault.schemas.VaultEntry(
                        hash = h,
                        fence_text = entry.get("fence_text") or "",
                        info_string = entry.get("info_string") or "",
                        lang = entry.get("lang") or "text",
                        line_count = int(entry.get("line_count") or 0),
                        char_count = int(entry.get("char_count") or 0),
                        sentinel_kind = entry.get(
                            "sentinel_kind", "fence_backtick",
                        ),
                    )
            coerced_vault[h] = entry
        hash_list = domains.dd.synth.nodes.vault.domain.format_entries_for_prompt(
            coerced_vault, hashes = allowed_hashes,
            max_chars_per_entry = MAX_VAULT_CHARS_PER_ENTRY,
            max_total_chars = vault_char_budget or MAX_VAULT_CHARS_TOTAL,
        )
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
            "  - GROUND IT CONCRETELY: name at least one real file path, "
            "directory name, config key, command, or setting the sources "
            "actually mention. 'This helps you understand the layout' is "
            "filler — 'settings.json lives under ~/.claude/' is not. If the "
            "sources genuinely give you nothing concrete for a subtopic, "
            "that's a signal to cut it and cover fewer, better-grounded "
            "subtopics instead.\n"
            "  - VARY THE OPENING of each explanation — reusing the same "
            "lead-in phrase across subtopics (e.g. every paragraph starting "
            "'Exploring the directory...') reads as templated filler, not "
            "distinct teaching points.\n"
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

        f"{prior_feedback_block}"
        f"{prose_note}"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION (H2): {section_id} — {section_heading}\n"
        f"SECTION GOAL: {section_description}\n"
        f"PREREQUISITES (already covered): {prereqs_str}\n\n"

        f"== GROUNDED CONTRIBUTIONS (your subtopics MUST cover these) ==\n"
        f"{format_contributions_block(contributions)}\n\n"

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
        f"{format_memory_block(memory)}\n\n"

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
    """MAMM-Refine critic (arXiv 2503.15272): structural summaries only (not full prose) — per §4, reranking > regeneration."""
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


def summarize_candidate(
    draft: schemas.LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    n_primary_contribs: int,
    vault_rich: dict | None = None,
) -> dict:
    """Compact candidate summary for critic picker (~250 tokens). Biases toward structure, not content, per outline_sdp's USC pattern."""
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


def dedupe_vault_hashes_across_sections(
    per_section_index: dict[str, list[dict]],
) -> tuple[int, int]:
    """Modify per_section_index in-place so each vault hash appears in at most one section. Winner = strongest relevance, then smallest pool, then sorted section_id. Returns (n_hashes_deduped, n_refs_removed)."""
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
        for sid, _rel in options:
            if sid == best_sid:
                continue
            for c in per_section_index[sid]:
                refs = c.get("code_refs") or []
                if h in refs:
                    c["code_refs"] = [r for r in refs if r != h]
                    n_refs_removed += 1
    return n_hashes_deduped, n_refs_removed


def placeholder_section(
    *,
    section_id: str,
    heading: str,
    n_repairs: int,
    deployment_writer: Optional[str],
    error_tags: Optional[list[str]] = None,
) -> schemas.Section:
    """Fallback when all writer drafts and repairs fail. Keeps chapter assemblable; empty subtopics triggers checklist density gate → mgsr_replan retargets or merges this section."""
    issues = ["placeholder"]
    if error_tags:
        # "draft_fail:<tag>" per failed attempt — lets sawc_write_run
        # aggregate an error_breakdown the same way digest_construct
        # already does, instead of a total failure being undiagnosable.
        issues.extend(f"draft_fail:{tag}" for tag in error_tags)
    return schemas.Section(
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
        n_drafts_tried=params.N_DRAFTS,
        n_repairs=n_repairs,
        deployment_writer=deployment_writer,
        issues=issues,
    )


def compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    digest_manifest_hash: str,
    refine_iter: int = 0,
) -> str:
    """Content-addressed cache key for SAWC. refine_iter included so each mgsr→sawc loop iteration gets fresh drafts (without it, cache short-circuits with stale results)."""
    payload = (
        f"outline={outline_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={versions.SAWC_PROMPT_VERSION}|"
        f"schema={versions.SAWC_SCHEMA_VERSION}|"
        f"iter={refine_iter}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


_RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}


def parse_json_response(text: str) -> Optional[dict]:
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


def try_parse_draft(
    raw: dict,
) -> tuple[Optional[schemas.LLMSectionDraft], Optional[str]]:
    try:
        return schemas.LLMSectionDraft.model_validate(raw), None
    except ValidationError as e:
        return None, shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def shorten_pydantic_error(e: ValidationError) -> str:
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


def load_sawc_payload(text: str) -> dict:
    """Parse the persisted sawc blob. Returns the full payload dict;
    downstream nodes pick the fields they need (sections, memory_final,
    coverage_stats, etc.)."""
    return json.loads(text)

