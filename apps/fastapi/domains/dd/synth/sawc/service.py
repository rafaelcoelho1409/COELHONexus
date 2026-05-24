"""sawc service — all function definitions.

v2 cookbook schema (2026-05-24 evening): output is structured as
{heading, intro, subtopics: [{subheading, explanation, code_ref_hash}],
citations}. Each subtopic renders as one H3 + 1-2 sentence prose +
ONE code block. See `sawc/types.py` and
`docs/KD-CODE-FIRST-IMPLEMENTATION-2026-05-24.md`.
"""
from __future__ import annotations

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
def validate_section_against_inputs(
    draft: _LLMSectionDraft,
    *,
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
) -> list[str]:
    """Cross-reference rules beyond per-field Pydantic format.

    Returns natural-language issue strings suitable for repair-prompt
    feedback. Empty list = clean.

    Catches:
      - heading drift (LLM didn't echo the outline heading verbatim)
      - hallucinated code_ref hashes (not in allowed_hashes)
      - hallucinated citation source_keys (not in valid_source_keys)
    """
    issues: list[str] = []

    if draft.heading.strip().casefold() != expected_heading.strip().casefold():
        issues.append(
            f"heading {draft.heading!r} doesn't match the outline heading "
            f"{expected_heading!r}. Echo the outline heading verbatim."
        )

    # v2 cookbook schema: validate subtopics' code_ref_hash field
    bad_hashes = [
        s.code_ref_hash for s in draft.subtopics
        if s.code_ref_hash not in allowed_hashes
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
    )
    # v2 cookbook scoring: subtopic count + explanation density + heading
    # match + citation count drive the structural score.
    n_vault_violations = sum(
        1 for s in draft.subtopics if s.code_ref_hash not in allowed_hashes
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
    n_sections_completed = sum(1 for s in sections if not s.issues)
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
) -> str:
    """Build the per-section per-draft writer prompt.

    Args:
        vault_rich: optional dict[hash → VaultEntry-like dict] giving the
            LLM full visibility into each allowed code block (Visible Vault,
            2026-05-24 Ship #1). When provided, renders `<code id=...>{body}
            </code>` envelopes so the LLM can pick pedagogically valuable
            hashes from informed context. When None, falls back to plain
            hash listing (legacy behavior).
    """
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites
        else "(none — this is a stage-0 section)"
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

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION (H2): {section_id} — {section_heading}\n"
        f"SECTION GOAL: {section_description}\n"
        f"PREREQUISITES (already covered): {prereqs_str}\n\n"

        f"== GROUNDED CONTRIBUTIONS (your subtopics MUST cover these) ==\n"
        f"{_format_contributions_block(contributions)}\n\n"

        f"== ALLOWED CODE BANK ({len(allowed_hashes)} entries) — these "
        f"are the actual code blocks available for THIS section. "
        f"Each `<code id=...>` envelope shows the FULL code body. PICK "
        f"3-8 BEST ONES — each becomes one subtopic. Reason about each "
        f"block fully; the explanation must reference specific lines / "
        f"decorators / arguments. ==\n"
        f"{hash_list}\n\n"

        f"== VALID CITATION SOURCE_KEYS ({len(valid_source_keys)}) — "
        f"citations.source_key MUST be one of these ==\n"
        f"{source_list}\n\n"

        f"== MEMORY (compressed prior-stage sections — already covered, "
        f"don't re-introduce) ==\n"
        f"{_format_memory_block(memory)}\n\n"

        f"== OUTPUT — strict JSON (cookbook v2 schema) ==\n"
        f"{{\n"
        f'  "heading":  "{section_heading}",  /* ECHO verbatim, no "# " */\n'
        f'  "intro":    "1-2 sentences (20-400 chars) framing what this '
        f'section covers and why the reader should care. NO code fences.",\n'
        f'  "subtopics": [\n'
        f'    {{\n'
        f'      "subheading":    "2-10 word descriptive H3 phrase, e.g. '
        f'\'Minimal Tool Definition\' or \'Async Tool with Context\'",\n'
        f'      "explanation":   "8-80 words. The prose BEFORE the code. '
        f'Tell the reader what they\'re about to see and which specific '
        f'lines/decorators/parameters matter. NO code fences in this '
        f'field — pure prose only.",\n'
        f'      "code_ref_hash": "16-hex hash from the code bank above"\n'
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
        f"2. **Each subtopic MUST have a unique code_ref_hash from the "
        f"   bank above**. Inventing or paraphrasing a hash is a hard "
        f"   violation.\n"
        f"3. **CODE DENSITY: at least 3 subtopics per section. Aim for "
        f"   4-6** when the bank has ≥6 entries; up to 8 when bank ≥20. "
        f"   The whole point is code-rich learning material.\n"
        f"4. EXPLANATIONS ARE TIGHT: 8-80 words per explanation (1-3 "
        f"   sentences). Reference specific lines/decorators/types from "
        f"   the code. NO multi-paragraph summaries — the reader is here "
        f"   for code, not a tour.\n"
        f"5. SUBHEADINGS ARE SPECIFIC: 'Minimal Tool with Type Hints' not "
        f"   'Example 1'. Reader scans subheadings as a TOC.\n"
        f"6. DISTINCT subheadings within the section — no two subtopics "
        f"   can share a subheading or share a code_ref_hash.\n"
        f"7. Every `citations[*].source_key` MUST be one of the valid "
        f"   source_keys above. Aim for {n_primary_contribs}+ citations "
        f"   (one per primary contribution).\n"
        f"8. NO inline `<code-ref hash=\"...\"/>` tags anywhere. NO "
        f"   ```code fences``` in `intro` or `explanation`. The renderer "
        f"   materializes code per-subtopic from `code_ref_hash`.\n"
        f"9. NO `# docs:` / `# src:` source-id leaks in prose. Use the "
        f"   typed `citations` field; the renderer emits proper footnotes.\n"
        f"10. Don't re-introduce terminology already in `memory[*]"
        f".key_terminology` above — assume the reader saw it. Reference "
        f"    by name; don't redefine.\n"
        f"11. PEDAGOGICAL ORDER: subtopics ordered easiest → most "
        f"    advanced. First subtopic = canonical/minimal example. "
        f"    Subsequent subtopics = primitives / recipes / edge cases. "
        f"    Optional last subtopic = counter-example / gotcha.\n\n"

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
) -> str:
    """Repair prompt — same context as writer prompt, plus the
    issue list, asking for a fixed version preserving good fields."""
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites else "(none)"
    )
    hash_list = (
        "\n".join(f"  - {h}" for h in allowed_hashes)
        if allowed_hashes else "  (none)"
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
        f'"code_ref_hash": "16-hex"}},\n'
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
