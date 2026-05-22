"""sawc service — all function definitions."""
from __future__ import annotations

import re

from .constants import (
    _MEMORY_SUMMARY_CHARS_MIN,
    _MEMORY_SUMMARY_CHARS_MAX,
    _MEMORY_TERM_CHARS_MAX,
    _MEMORY_TERMS_MAX,
)
from .types import (
    _LLMSectionDraft,
    Citation,
    CodeRef,
    MemoryEntry,
    SAWCStats,
    Section,
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
    # --- summary: first paragraph, trimmed ---
    summary = (section.paragraphs[0] if section.paragraphs else "").strip()
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

    bad_hashes = [c.hash for c in draft.code_refs if c.hash not in allowed_hashes]
    if bad_hashes:
        issues.append(
            f"code_refs use hashes not in allowed_hashes: {bad_hashes}. "
            f"Pick ONLY from the allowed_hashes list shown in the prompt."
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
    n_vault_violations = sum(
        1 for c in draft.code_refs if c.hash not in allowed_hashes
    )
    n_citation_violations = sum(
        1 for c in draft.citations if c.source_key not in valid_source_keys
    )
    heading_mismatch = (
        draft.heading.strip().casefold() != expected_heading.strip().casefold()
    )

    total_chars = sum(len(p) for p in draft.paragraphs)
    n_paragraphs = len(draft.paragraphs)
    n_citations = len(draft.citations)

    score = 5.0
    score -= 10.0 * n_vault_violations
    score -= 10.0 * n_citation_violations
    score -= 5.0 if heading_mismatch else 0.0
    if n_primary_contribs > 0:
        score += 5.0 * min(n_citations / n_primary_contribs, 1.0)
    score += 3.0 * min(n_paragraphs / 5.0, 1.0)
    score -= 2.0 * max(0, n_paragraphs - 12)
    score += 2.0 * min(total_chars / 1500.0, 2.0)
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
    total_paragraphs = sum(len(s.paragraphs) for s in sections)
    total_code_refs = sum(len(s.code_refs) for s in sections)
    total_citations = sum(len(s.citations) for s in sections)
    n_para_total_chars = sum(
        len(p) for s in sections for p in s.paragraphs
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
        total_paragraphs=total_paragraphs,
        total_code_refs=total_code_refs,
        total_citations=total_citations,
        avg_paragraphs_per_section=(
            total_paragraphs / n_sections if n_sections else 0.0
        ),
        avg_chars_per_paragraph=(
            n_para_total_chars / total_paragraphs if total_paragraphs else 0.0
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
) -> str:
    """Build the per-section per-draft writer prompt."""
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites
        else "(none — this is a stage-0 section)"
    )
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
        f"synth pipeline. Write ONE section of one chapter, grounded in "
        f"the per-source digest the previous step (digest_construct) "
        f"already produced. This is one of N=3 best-of-N drafts; a "
        f"critic LLM will pick the best one afterwards (MAMM-Refine "
        f"arXiv 2503.15272 pattern).\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION: {section_id} — {section_heading}\n"
        f"SECTION GOAL: {section_description}\n"
        f"PREREQUISITES (already covered): {prereqs_str}\n\n"

        f"== GROUNDED CONTRIBUTIONS (your prose MUST cover these) ==\n"
        f"{_format_contributions_block(contributions)}\n\n"

        f"== ALLOWED VAULT HASHES ({len(allowed_hashes)}) — pick a subset "
        f"to place in this section ==\n"
        f"{hash_list}\n\n"

        f"== VALID CITATION SOURCE_KEYS ({len(valid_source_keys)}) — "
        f"citations.source_key MUST be one of these ==\n"
        f"{source_list}\n\n"

        f"== MEMORY (compressed prior-stage sections — already covered, "
        f"don't re-introduce) ==\n"
        f"{_format_memory_block(memory)}\n\n"

        f"== OUTPUT — strict JSON ==\n"
        f"{{\n"
        f'  "heading":    "{section_heading}",  /* ECHO verbatim */\n'
        f'  "paragraphs": [\n'
        f'    "First paragraph: open with the section\'s framing (no '
        f'redundant chapter intro). 80-1800 chars. NO embedded \\\\n\\\\n.",\n'
        f'    "Subsequent paragraphs: dense technical prose grounded in '
        f'the contributions above.",\n'
        f'    ... 2-12 entries ...\n'
        f'  ],\n'
        f'  "code_refs": [\n'
        f'    {{"hash": "16-hex", "placement_hint": "after paragraph 2"}},\n'
        f'    ...\n'
        f'  ],\n'
        f'  "citations": [\n'
        f'    {{"source_key": "ingestion/.../0024-isbn.md", '
        f'"claim": "restate the specific fact this source backs"}},\n'
        f'    ...\n'
        f'  ]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. `heading` MUST be EXACTLY {section_heading!r} (case-sensitive "
        f"   echo of the outline).\n"
        f"2. Every `code_refs[*].hash` MUST be in the allowed_hashes list "
        f"   above. Inventing or 'paraphrasing' a hash is a violation.\n"
        f"3. Every `citations[*].source_key` MUST be one of the valid "
        f"   source_keys above. Aim for {n_primary_contribs}+ citations "
        f"   (one per primary contribution).\n"
        f"4. `paragraphs` is a LIST. Each entry is ONE paragraph — do NOT "
        f"   embed `\\n\\n` inside a single entry. The renderer joins "
        f"   with `\\n\\n` later.\n"
        f"5. NO inline `<code-ref hash=\"...\"/>` tags in prose. Use the "
        f"   typed `code_refs` field; the renderer places the block at "
        f"   the right paragraph boundary.\n"
        f"6. NO `# docs:` / `# src:` source-id leaks in prose. Use the "
        f"   typed `citations` field; the renderer emits proper footnotes.\n"
        f"7. Don't re-introduce terminology already in `memory[*]"
        f".key_terminology` above — assume the reader saw it. Reference "
        f"   by name; don't redefine.\n"
        f"8. Dense, production-focused. Concrete > abstract. Name actual "
        f"   APIs / methods / types — match the granularity of `key_facts`.\n\n"

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
            f"  [{i}] paragraphs={c.get('n_paragraphs')}, "
            f"total_chars={c.get('total_chars')}, "
            f"avg_chars/para={c.get('avg_chars_per_para', 0):.0f}, "
            f"code_refs={c.get('n_code_refs')}, "
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
        f"1. ZERO violations (vault hashes outside allowed, citations "
        f"   outside valid source_keys, heading mismatch). A candidate "
        f"   with any violations LOSES to any clean candidate.\n"
        f"2. Citation count near or above n_primary_contribs="
        f"{n_primary_contribs} (one citation per primary contribution).\n"
        f"3. Paragraph density in sweet spot: 3-8 paragraphs, "
        f"   200-1500 chars each (avg_chars/para 250-700 is healthy).\n"
        f"4. Highest structural_score (a deterministic proxy combining "
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
        f"Fix structural issues in this section draft. Keep the same JSON "
        f"schema. Preserve good paragraphs and citations; ONLY change what's "
        f"needed to clear the issues below.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION: {section_id} — {section_heading}\n"
        f"GOAL: {section_description}\n"
        f"PREREQUISITES: {prereqs_str}\n\n"

        f"ALLOWED VAULT HASHES (use ONLY these for code_refs):\n"
        f"{hash_list}\n\n"

        f"VALID CITATION SOURCE_KEYS (use ONLY these for citations):\n"
        f"{source_list}\n\n"

        f"CONTRIBUTIONS (for grounding):\n"
        f"{_format_contributions_block(contributions)}\n\n"

        f"MEMORY:\n{_format_memory_block(memory)}\n\n"

        f"CURRENT DRAFT:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Respond ONLY with valid JSON matching the original schema. "
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
    total_chars = sum(len(p) for p in draft.paragraphs)
    n_paragraphs = len(draft.paragraphs)
    avg_chars = (total_chars / n_paragraphs) if n_paragraphs else 0.0
    structural_score = score_draft_structural(
        draft,
        expected_heading=expected_heading,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        n_primary_contribs=n_primary_contribs,
    )
    return {
        "n_paragraphs":         n_paragraphs,
        "total_chars":          total_chars,
        "avg_chars_per_para":   avg_chars,
        "n_code_refs":          len(draft.code_refs),
        "n_citations":          len(draft.citations),
        "heading_match":        (
            draft.heading.strip().casefold()
            == expected_heading.strip().casefold()
        ),
        "structural_score":     structural_score,
        "violations":           issues,
    }
