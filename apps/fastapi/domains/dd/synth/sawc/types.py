"""sawc types — Pydantic models only."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .constants import (
    SAWC_SCHEMA_VERSION,
    SAWC_PROMPT_VERSION,
    _N_DRAFTS,
    _PARAGRAPHS_MIN,
    _PARAGRAPHS_MAX,
    _PARAGRAPH_CHARS_MIN,
    _PARAGRAPH_CHARS_MAX,
    _HEADING_MIN_WORDS,
    _HEADING_MAX_WORDS,
    _CODE_REFS_MAX,
    _CITATIONS_MIN,
    _CITATIONS_MAX,
    _CITATION_CLAIM_CHARS_MIN,
    _CITATION_CLAIM_CHARS_MAX,
    _PLACEMENT_HINT_CHARS_MIN,
    _PLACEMENT_HINT_CHARS_MAX,
    _MEMORY_TERMS_MIN,
    _MEMORY_TERMS_MAX,
    _MEMORY_TERM_CHARS_MIN,
    _MEMORY_TERM_CHARS_MAX,
    _MEMORY_SUMMARY_CHARS_MIN,
    _MEMORY_SUMMARY_CHARS_MAX,
    _HASH_RE,
)


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
