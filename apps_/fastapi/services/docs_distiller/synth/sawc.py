"""sawc_write — Structure-Aware Writing Controller library.

Pure module: Pydantic schemas + prompt templates + deterministic
validators + memory-ledger helpers + structural draft scoring.
No I/O, no LLM calls — that lives in `synth/nodes/sawc_write.py`.

ARCHITECTURE — combines three published patterns

  1. SurveyGen-I PlanEvo SAWC (arXiv 2508.14317 §3.2)
     - Stage-parallel scheduling: `for t = 0..max τ; for all s_i ∈ S_t`
       sequential across stages, parallel within stage
     - Memory ledger ℳ: accumulates prior section content + extracted
       terminology; later-stage sections see compressed memory of
       earlier-stage sections so cross-section coherence holds
     - Ablation evidence: w/o plan-update drops STRUC by 0.42 / CONSIS
       by 0.28 → memory is what keeps structural cohesion

  2. MAMM-Refine multi-agent recipe (arXiv 2503.15272 §4)
     - N=3 writer drafts from distinct rotator picks (writer pool)
     - 1 critic-picker call from a DIFFERENT model family (critic pool)
       → less-correlated errors than dual-same-family
     - Picker as RERANKER, not regenerator (paper finding: rerank > regen)
     - Constraint: similar-capability agents; large ability gaps reduce
       gains. Our writer/critic pools are all "free-tier frontier" so
       this holds.

  3. Self-Certainty (arXiv 2502.18581, Feb 2025)
     - Reward-free best-of-N selection via output probability distribution
     - We use a STRUCTURAL scoring proxy (no logprobs available from the
       bandit rotator) as the picker-fallback when the critic LLM fails
       — same shape: pick by scalar quality estimate

WHAT IT REPLACES (deprecated Phase C + Phase D + scrubber passes 3-7)

  Deprecated Phase C / Phase D:
    - synthesize_one_section() — single LLM call per section
    - prose_md: str — raw markdown, prone to literal \\n\\n bugs
    - code_refs: list[str] — flat list, no typed semantics
    - Hash whitelist from Phase B cosine routing — known mis-routing source
    - Free-form `assumes_from_prior_sections` prose string — no machine
      structure for MGSR to replan over

  New sawc_write:
    - paragraphs: list[str] — typed list; join with "\\n\\n" at render
      → CAN'T have literal "\\n\\n" bugs because paragraphs are separate
      string elements, not embedded markers
    - code_refs: list[CodeRef] — typed objects with hash + placement_hint
    - citations: list[Citation] — typed; source_key + claim grounded to digest
    - Hash whitelist from digest_construct per_section.code_refs (LLM-
      grounded, not cosine)
    - Memory ledger as list[MemoryEntry] (JSON-structured for MGSR replan)

INPUTS / OUTPUTS

  Inputs:
    - outline-latest.json   (from outline_sdp): sections + DAG stages
    - digest-latest.json    (from digest_construct): per_section index +
                              coverage stats

  Per-section LLM output (_LLMSectionDraft):
    heading, paragraphs, code_refs, citations

  Persisted (ChapterDraft):
    sections (list[Section] with all LLM fields + node-injected meta),
    memory_final (list[MemoryEntry] as ledger stood at end),
    challenges + flashcards (passed through from outline),
    coverage_stats, manifest_hashes

DOWNSTREAM CONSUMERS

  - `checklist_eval` reads `sections` + applies ~10 binary criteria
  - `mgsr_replan` reads `memory_final` + `coverage_stats` + per-section
    issues to emit replan actions
  - `render_audit_write` reads everything + materializes vault sentinels
    to markdown + runs round-trip audit

TUNABLES

  _N_DRAFTS                 = 3        (MAMM-recommended)
  _MAX_REPAIR_ATTEMPTS      = 2
  _PARAGRAPHS_MIN/MAX       = 2 / 12
  _PARAGRAPH_CHARS_MIN/MAX  = 80 / 1800
  _CITATIONS_MIN/MAX        = 0 / 12   (allow 0 for purely structural
                                          sections; checklist_eval flags
                                          weak-citation sections later)
  _MEMORY_TERMS_MIN/MAX     = 0 / 12   (terminology per memory entry)
  _MEMORY_SUMMARY_CHARS     = 40 / 600
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Versioning + tunables
# =============================================================================
SAWC_SCHEMA_VERSION = "1.0"
SAWC_PROMPT_VERSION = "v1-2026-05-19"

_N_DRAFTS = 3
_MAX_REPAIR_ATTEMPTS = 2
_PARAGRAPHS_MIN = 2
_PARAGRAPHS_MAX = 12
_PARAGRAPH_CHARS_MIN = 80
_PARAGRAPH_CHARS_MAX = 1800
_HEADING_MIN_WORDS = 2
_HEADING_MAX_WORDS = 8
_CODE_REFS_MAX = 30
_CITATIONS_MIN = 0
_CITATIONS_MAX = 12
_CITATION_CLAIM_CHARS_MIN = 6
_CITATION_CLAIM_CHARS_MAX = 400
_PLACEMENT_HINT_CHARS_MIN = 4
_PLACEMENT_HINT_CHARS_MAX = 200
_MEMORY_TERMS_MIN = 0
_MEMORY_TERMS_MAX = 12
_MEMORY_TERM_CHARS_MIN = 2
_MEMORY_TERM_CHARS_MAX = 80
_MEMORY_SUMMARY_CHARS_MIN = 40
_MEMORY_SUMMARY_CHARS_MAX = 600

_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")


# =============================================================================
# Pydantic — LLM output side
# =============================================================================
class Citation(BaseModel):
    """One citation: source the writer relied on, plus the specific claim
    it backs. Grounds prose to digest_construct's per-source key_facts."""
    source_key: str = Field(
        description=(
            "MinIO source key (e.g. `ingestion/pydantic/pages/0024-isbn.md`). "
            "MUST match one of the digest's per_source.source_key entries."
        ),
    )
    claim: str = Field(
        description=(
            "6-400 chars. The specific claim this source backs in the prose. "
            "Should restate a key_fact from the digest, not paraphrase the "
            "entire section. Used by render_audit_write to emit `[source]` "
            "footnotes; by checklist_eval to verify cites_at_least_N."
        ),
    )

    @field_validator("claim")
    @classmethod
    def _validate_claim(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_CITATION_CLAIM_CHARS_MIN <= len(s) <= _CITATION_CLAIM_CHARS_MAX):
            raise ValueError(
                f"citation.claim must be {_CITATION_CLAIM_CHARS_MIN}-"
                f"{_CITATION_CLAIM_CHARS_MAX} chars; got {len(s)}"
            )
        return s


class CodeRef(BaseModel):
    """A vault sentinel placed in the section. Typed so render_audit_write
    can inject the code block at the right paragraph boundary without
    parsing inline markers in the prose."""
    hash: str = Field(
        description=(
            "16-hex vault hash. MUST be one of `allowed_hashes` for this "
            "section (subset of digest's per_section[section_id] code_refs)."
        ),
    )
    placement_hint: str = Field(
        description=(
            "4-200 chars. WHERE in the paragraph sequence this code block "
            "should be injected, e.g. 'after paragraph 2', 'before "
            "paragraph 4', 'at end'. render_audit_write reads this as a "
            "soft signal — if ambiguous, the renderer falls back to "
            "appending all code refs at the section end."
        ),
    )

    @field_validator("hash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        if not _HASH_RE.match(v):
            raise ValueError(
                f"code_ref.hash {v!r} must be 16 lowercase hex chars"
            )
        return v

    @field_validator("placement_hint")
    @classmethod
    def _validate_hint(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_PLACEMENT_HINT_CHARS_MIN <= len(s) <= _PLACEMENT_HINT_CHARS_MAX):
            raise ValueError(
                f"placement_hint must be {_PLACEMENT_HINT_CHARS_MIN}-"
                f"{_PLACEMENT_HINT_CHARS_MAX} chars; got {len(s)}"
            )
        return s


class _LLMSectionDraft(BaseModel):
    """What the LLM emits per writer-call. Plus node-injected metadata
    becomes a `Section` for persistence."""
    heading: str = Field(
        description=(
            "2-8 words. ECHO the outline heading verbatim — do not reword. "
            "render_audit_write asserts heading matches outline; mismatch "
            "fails the round-trip audit."
        ),
    )
    paragraphs: list[str] = Field(
        description=(
            f"{_PARAGRAPHS_MIN}-{_PARAGRAPHS_MAX} paragraphs. Each entry is "
            f"ONE paragraph (no embedded `\\n\\n`). Each "
            f"{_PARAGRAPH_CHARS_MIN}-{_PARAGRAPH_CHARS_MAX} chars. Dense, "
            f"production-focused prose grounded in the contributions + "
            f"memory shown in the prompt. NO source-id leakage like "
            f"`# docs: foo`; cite via `citations` instead. NO inline "
            f"`<code-ref hash=...>` tags; use `code_refs` instead."
        ),
    )
    code_refs: list[CodeRef] = Field(
        default_factory=list,
        description=(
            f"0-{_CODE_REFS_MAX} typed vault references. Each `hash` MUST "
            f"be in the section's allowed_hashes (passed in the prompt). "
            f"Unknown hashes are a hard violation — the LLM must NOT "
            f"invent hashes. Listing every allowed hash isn't required; "
            f"list the ones the section's prose actually discusses."
        ),
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description=(
            f"{_CITATIONS_MIN}-{_CITATIONS_MAX} citations. Each `source_key` "
            f"MUST be one of the digest's per_source source_keys. The "
            f"`claim` should restate the specific key_fact the source backs."
        ),
    )

    @field_validator("heading")
    @classmethod
    def _validate_heading(cls, v: str) -> str:
        words = v.strip().split()
        if not (_HEADING_MIN_WORDS <= len(words) <= _HEADING_MAX_WORDS):
            raise ValueError(
                f"heading must be {_HEADING_MIN_WORDS}-{_HEADING_MAX_WORDS} "
                f"words; got {len(words)} ({v!r})"
            )
        if v.lstrip().startswith("#"):
            raise ValueError("heading must NOT start with '#'")
        return v.strip()

    @field_validator("paragraphs")
    @classmethod
    def _validate_paragraphs(cls, v: list[str]) -> list[str]:
        if not (_PARAGRAPHS_MIN <= len(v) <= _PARAGRAPHS_MAX):
            raise ValueError(
                f"paragraphs count must be {_PARAGRAPHS_MIN}-"
                f"{_PARAGRAPHS_MAX}; got {len(v)}"
            )
        cleaned: list[str] = []
        for i, p in enumerate(v):
            # Collapse leading/trailing whitespace but PRESERVE internal
            # single \n (Markdown line breaks within a paragraph). Reject
            # embedded \n\n which is the bug we're trying to prevent.
            s = p.strip()
            if "\n\n" in s:
                raise ValueError(
                    f"paragraph[{i}] contains embedded blank line "
                    f"(`\\n\\n`) — use SEPARATE paragraphs list entries "
                    f"instead. Found in {s[:60]!r}"
                )
            if not (_PARAGRAPH_CHARS_MIN <= len(s) <= _PARAGRAPH_CHARS_MAX):
                raise ValueError(
                    f"paragraph[{i}] length must be {_PARAGRAPH_CHARS_MIN}"
                    f"-{_PARAGRAPH_CHARS_MAX} chars; got {len(s)}"
                )
            # No `# docs:` leakage (caught in render audit too, but
            # rejecting at validation lets the repair loop see it)
            if re.search(r"(?m)^\s*#\s*docs?\s*:", s):
                raise ValueError(
                    f"paragraph[{i}] contains a `# docs:` source-id "
                    f"leak. Use `citations` instead — that field is "
                    f"rendered as proper footnotes downstream."
                )
            # No inline vault sentinels — they should be in code_refs
            if "<code-ref" in s:
                raise ValueError(
                    f"paragraph[{i}] contains an inline `<code-ref ...>` "
                    f"sentinel. Use `code_refs` instead so the renderer "
                    f"can inject the code block cleanly."
                )
            cleaned.append(s)
        return cleaned

    @field_validator("code_refs")
    @classmethod
    def _validate_refs(cls, v: list[CodeRef]) -> list[CodeRef]:
        if len(v) > _CODE_REFS_MAX:
            raise ValueError(
                f"code_refs count {len(v)} exceeds max {_CODE_REFS_MAX}"
            )
        hashes = [c.hash for c in v]
        if len(set(hashes)) != len(hashes):
            raise ValueError(f"duplicate code_ref hashes: {hashes}")
        return v

    @field_validator("citations")
    @classmethod
    def _validate_citations(cls, v: list[Citation]) -> list[Citation]:
        if not (_CITATIONS_MIN <= len(v) <= _CITATIONS_MAX):
            raise ValueError(
                f"citations count must be {_CITATIONS_MIN}-"
                f"{_CITATIONS_MAX}; got {len(v)}"
            )
        return v


# =============================================================================
# Pydantic — persisted side (LLM + node-injected fields)
# =============================================================================
class Section(BaseModel):
    """Persisted section. LLM fields from _LLMSectionDraft + node-injected
    observability/meta."""
    section_id:        str
    heading:           str
    paragraphs:        list[str]
    code_refs:         list[CodeRef]
    citations:         list[Citation]
    # node-injected (post-LLM):
    wall_ms:           Optional[int] = None
    deployment_writer: Optional[str] = None
    deployment_critic: Optional[str] = None
    n_drafts_tried:    int = _N_DRAFTS
    n_repairs:         int = 0
    chosen_draft_idx:  Optional[int] = None
    structural_score:  Optional[float] = None
    fallback_picker:   Optional[Literal["self_certainty", "structural_score"]] = None
    issues:            list[str] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    """One row of the SAWC memory ledger ℳ. Compressed representation of a
    written section, used by later-stage sections to maintain cross-section
    coherence + by mgsr_replan as input.

    Per SurveyGen-I §3.2.2, the ledger accumulates BOTH "draft content"
    and "key domain-specific terminology". For v1 the extraction is
    DETERMINISTIC (no extra LLM call) — derived from the digest's
    contributions for the section. mgsr_replan / checklist_eval may later
    swap in LLM-based extraction if needed."""
    section_id:       str
    heading:          str
    summary:          str             # 40-600 chars
    key_terminology:  list[str]       # 0-12 entries, each 2-80 chars

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_MEMORY_SUMMARY_CHARS_MIN <= len(s) <= _MEMORY_SUMMARY_CHARS_MAX):
            raise ValueError(
                f"memory summary must be {_MEMORY_SUMMARY_CHARS_MIN}-"
                f"{_MEMORY_SUMMARY_CHARS_MAX} chars; got {len(s)}"
            )
        return s

    @field_validator("key_terminology")
    @classmethod
    def _validate_terms(cls, v: list[str]) -> list[str]:
        if not (_MEMORY_TERMS_MIN <= len(v) <= _MEMORY_TERMS_MAX):
            raise ValueError(
                f"key_terminology count must be {_MEMORY_TERMS_MIN}-"
                f"{_MEMORY_TERMS_MAX}; got {len(v)}"
            )
        cleaned: list[str] = []
        for t in v:
            s = " ".join(t.strip().split())
            if not (_MEMORY_TERM_CHARS_MIN <= len(s) <= _MEMORY_TERM_CHARS_MAX):
                raise ValueError(
                    f"term length must be {_MEMORY_TERM_CHARS_MIN}-"
                    f"{_MEMORY_TERM_CHARS_MAX} chars; got {len(s)} ({t!r})"
                )
            cleaned.append(s)
        # case-fold-dedupe
        seen: set[str] = set()
        out: list[str] = []
        for t in cleaned:
            key = t.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out


class SAWCStats(BaseModel):
    """Aggregate observability stats over the chapter draft."""
    n_sections:           int
    n_sections_completed: int   # passed Pydantic + cross-ref clean
    n_sections_fallback:  int   # placeholder used (all 3 drafts failed)
    n_stages:             int
    n_total_drafts_fired: int   # sum of writer calls across all sections
    n_critic_picks:       int   # sum of critic-picker calls
    n_picker_fallbacks:   int   # times we fell back to structural scoring
    n_repairs:            int
    total_paragraphs:     int
    total_code_refs:      int
    total_citations:      int
    avg_paragraphs_per_section: float
    avg_chars_per_paragraph:    float


class ChapterDraft(BaseModel):
    """Full chapter draft — what gets persisted to MinIO as sawc-latest.json."""
    schema_version: str = SAWC_SCHEMA_VERSION
    prompt_version: str = SAWC_PROMPT_VERSION
    chapter_id:     str
    chapter_title:  str
    framework_slug: str
    sections:       list[Section]      # in stage order, then DAG order within stage
    memory_final:   list[MemoryEntry]  # ledger as it stood at end
    challenges:     list[str]          # passed through from outline_sdp
    flashcards:     list[dict]         # passed through (dict form to avoid Flashcard import)
    coverage_stats: SAWCStats


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
