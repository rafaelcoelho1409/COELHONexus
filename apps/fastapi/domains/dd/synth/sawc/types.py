"""sawc types — Pydantic models (v2 cookbook schema).

2026-05-24 evening (KD-CODE-FIRST-IMPLEMENTATION): replaced the v1 flat
schema (paragraphs + code_refs as separate lists) with a cookbook-style
nested structure where each subtopic IS a 1:1 pair of (explanation, code
block). The renderer emits one H3 per subtopic with the explanation
appearing BEFORE the materialized code block.

Why this schema change:
  - Empirical run 1 (pre-Phase 1): 0 code fences total. LLM emitted all-
    prose paragraphs and zero code_refs.
  - Empirical run 2 (post-Phase 1 bank-augmentation): 216 code fences
    across 3 chapters, 65-83% code density. BUT structure was flat —
    H2 sections with multiple code blocks interleaved freely with prose,
    no H3 subsections.
  - v2 schema enforces the user-requested pattern: H2 topic → for each
    subtopic in 3-12 picks: (H3 subheading, 1-2 sentence explanation,
    one code block).
  - Pydantic min_length=3 + required `code_ref_hash` per Subtopic makes
    code-emission STRUCTURALLY MANDATORY — no fallback to all-prose.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .constants import (
    SAWC_SCHEMA_VERSION,
    SAWC_PROMPT_VERSION,
    _N_DRAFTS,
    _HEADING_MIN_WORDS,
    _HEADING_MAX_WORDS,
    _CITATIONS_MIN,
    _CITATIONS_MAX,
    _CITATION_CLAIM_CHARS_MIN,
    _CITATION_CLAIM_CHARS_MAX,
    _MEMORY_TERMS_MIN,
    _MEMORY_TERMS_MAX,
    _MEMORY_TERM_CHARS_MIN,
    _MEMORY_TERM_CHARS_MAX,
    _MEMORY_SUMMARY_CHARS_MIN,
    _MEMORY_SUMMARY_CHARS_MAX,
    _HASH_RE,
    _SUBTOPICS_MIN,
    _SUBTOPICS_MAX,
    _SUBHEADING_MIN_WORDS,
    _SUBHEADING_MAX_WORDS,
    _EXPLANATION_WORDS_MIN,
    _EXPLANATION_WORDS_MAX,
    _INTRO_CHARS_MIN,
    _INTRO_CHARS_MAX,
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


class Subtopic(BaseModel):
    """One (subheading, explanation, code block) triple. Each Subtopic
    renders as a single H3 subsection in the final chapter — the
    explanation appears immediately BEFORE the materialized code.

    code_source semantics (2026-05-24, Ship #95 sawc_derive):
      - "verbatim": code body is materialized from vault[code_ref_hash]
        byte-exact at render time (Yeung 2025 Deterministic Quoting).
        This is the default — the LLM picked a vault hash; render audit
        re-hashes the substitution to guarantee byte fidelity.
      - "derived": code body is AI-generated via sawc_derive
        (Analogical Prompting + MPSC). code_ref_hash still points to the
        ORIGINATING vault entry (typically a thin signature) for trace-
        ability; derived_code carries the expanded runnable example.
        Render audit AST-parses derived_code to filter hallucinations.

    code_ref_hash REMAINS REQUIRED in both cases — every subtopic must
    anchor to a vault entry from the source docs (the derived case
    expands the original; never floats free of provenance).
    """
    # FIELD ORDER MATTERS for LLM generation. The Pydantic order matches
    # the JSON output order the LLM emits, and 2026 SOTA (Citation-
    # Grounded Code Comprehension, arXiv 2512.12117; NL Outlines, arXiv
    # 2408.04820) shows that committing to the cited entity BEFORE writing
    # prose dramatically reduces drift — the prose then conditions on a
    # KNOWN code body instead of an imagined topic.
    code_ref_hash: str = Field(
        default="",
        description=(
            "16-hex vault hash — one code block per Subtopic. PICK THIS "
            "FIRST. MUST be in the section's allowed_hashes shown in the "
            "prompt. For 'verbatim' subtopics, the renderer materializes the "
            "code from vault[code_ref_hash]. For 'derived' subtopics, the "
            "hash anchors provenance back to the originating docs entry. "
            "PROSE PATH (2026-05-30): leave EMPTY (\"\") for a conceptual / "
            "prose-only section that has NO code in its sources — the "
            "subtopic then renders as subheading + explanation with no code "
            "block. Only allowed when the prompt says PROSE MODE."
        ),
    )
    subheading: str = Field(
        description=(
            f"{_SUBHEADING_MIN_WORDS}-{_SUBHEADING_MAX_WORDS} words. The H3 "
            f"subheading naming WHAT THE CHOSEN code_ref_hash BLOCK actually "
            f"demonstrates — derive it from identifiers / decorators / "
            f"function names in the picked code, NOT from the broader topic "
            f"you might want to cover. Example: a code block defining "
            f"`@mcp.tool def list_skills(...)` becomes 'List Skills via "
            f"@mcp.tool'; a `roots=[Path(...)]` constructor call becomes "
            f"'Multi-Root Provider Construction'. NOT 'Example' or generic "
            f"labels."
        ),
    )
    explanation: str = Field(
        description=(
            f"{_EXPLANATION_WORDS_MIN}-{_EXPLANATION_WORDS_MAX} words. The "
            f"concise explanation that appears BEFORE the code block. MUST "
            f"reference at least one specific identifier visible in the "
            f"chosen code_ref_hash body (function name, decorator, type, or "
            f"parameter), so the prose grounds to the code below it. NO "
            f"code fences inside; this is prose only. Do NOT describe APIs "
            f"that aren't visible in the code — the validator rejects "
            f"prose that mentions zero code identifiers."
        ),
    )
    code_source: Literal["verbatim", "derived"] = Field(
        default="verbatim",
        description=(
            "Provenance tag. 'verbatim' = vault[hash] substituted byte-"
            "exact at render time (default). 'derived' = AI-generated by "
            "sawc_derive (Analogical Prompting + MPSC) when the originating "
            "vault entry was too thin (signature-only) to teach effectively. "
            "Derived subtopics render with a visible badge."
        ),
    )
    derived_code: Optional[str] = Field(
        default=None,
        description=(
            "When code_source='derived', the AI-generated runnable code "
            "body. Required if code_source='derived'; MUST be None if "
            "code_source='verbatim'. Audited via Python AST parse at "
            "render time — failures are reclassified as 'hallucinated' "
            "and drop the audit."
        ),
    )

    @field_validator("subheading")
    @classmethod
    def _validate_subheading(cls, v: str) -> str:
        s = v.strip()
        if s.lstrip().startswith("#"):
            raise ValueError("subheading must NOT start with '#'")
        words = s.split()
        if not (_SUBHEADING_MIN_WORDS <= len(words) <= _SUBHEADING_MAX_WORDS):
            raise ValueError(
                f"subheading must be {_SUBHEADING_MIN_WORDS}-"
                f"{_SUBHEADING_MAX_WORDS} words; got {len(words)} ({s!r})"
            )
        return s

    @field_validator("explanation")
    @classmethod
    def _validate_explanation(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        words = s.split()
        if not (_EXPLANATION_WORDS_MIN <= len(words) <= _EXPLANATION_WORDS_MAX):
            raise ValueError(
                f"explanation must be {_EXPLANATION_WORDS_MIN}-"
                f"{_EXPLANATION_WORDS_MAX} words; got {len(words)}"
            )
        if "```" in s or "<code-ref" in s or "<code id" in s:
            raise ValueError(
                "explanation must NOT contain code fences or inline code "
                "envelope tags — it's prose-only and renders BEFORE the code"
            )
        if re.search(r"(?m)^\s*#\s*docs?\s*:", s):
            raise ValueError(
                "explanation contains a `# docs:` source-id leak; "
                "use the `citations` field for source references."
            )
        return s

    @field_validator("code_ref_hash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        # Empty = PROSE subtopic (conceptual/no-code section; see the prose
        # path note in the field description). Non-empty must be 16-hex.
        if v and not _HASH_RE.match(v):
            raise ValueError(
                f"code_ref_hash {v!r} must be 16 lowercase hex chars "
                f"(or empty \"\" for a prose subtopic)"
            )
        return v

    @field_validator("derived_code")
    @classmethod
    def _validate_derived_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        s = v.rstrip()
        if not s.strip():
            raise ValueError("derived_code, when set, must be non-empty")
        # Coarse size guard — derived bodies are pedagogical examples,
        # not full programs. The detailed AST/structure audit lives in
        # render_audit_write.
        if len(s) > 8000:
            raise ValueError(
                f"derived_code too long ({len(s)} chars; max 8000)"
            )
        return s

    @model_validator(mode="after")
    def _validate_source_code_pair(self) -> "Subtopic":
        if self.code_source == "derived":
            if not self.derived_code:
                raise ValueError(
                    "code_source='derived' requires non-empty derived_code"
                )
        else:  # verbatim
            if self.derived_code is not None:
                raise ValueError(
                    "code_source='verbatim' must NOT set derived_code "
                    "(use code_source='derived' or leave derived_code=None)"
                )
        return self


class _LLMSectionDraft(BaseModel):
    """Cookbook-style section schema (v2). The LLM emits a sequence of
    Subtopic triples — each becoming one (H3 subheading, explanation, code
    block) unit in the rendered chapter. Pydantic floors enforce the
    code-first contract: at least 3 subtopics, each REQUIRED to cite a
    vault hash. Plus node-injected metadata becomes a `Section` for
    persistence."""
    heading: str = Field(
        description=(
            f"{_HEADING_MIN_WORDS}-{_HEADING_MAX_WORDS} words. ECHO the "
            f"outline H2 heading verbatim — do not reword. "
            f"render_audit_write asserts heading matches outline; mismatch "
            f"fails the round-trip audit."
        ),
    )
    intro: str = Field(
        description=(
            f"{_INTRO_CHARS_MIN}-{_INTRO_CHARS_MAX} chars. 1-2 sentences "
            f"framing what this H2 section covers and why the reader "
            f"should care. Sets up the subtopics that follow. NO code "
            f"fences, NO inline backticks for full APIs (mention concepts, "
            f"not specific code — that's what the subtopics do)."
        ),
    )
    subtopics: list[Subtopic] = Field(
        description=(
            f"{_SUBTOPICS_MIN}-{_SUBTOPICS_MAX} Subtopic triples. Each is "
            f"(subheading, explanation, code_ref_hash) — rendered as one H3 "
            f"+ 1-2 prose sentences + ONE code block. The reader should be "
            f"able to scan the H3 headings as a mini-TOC for this H2 "
            f"section. Aim for 4-6 subtopics in most sections."
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

    @field_validator("intro")
    @classmethod
    def _validate_intro(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_INTRO_CHARS_MIN <= len(s) <= _INTRO_CHARS_MAX):
            raise ValueError(
                f"intro must be {_INTRO_CHARS_MIN}-{_INTRO_CHARS_MAX} "
                f"chars; got {len(s)}"
            )
        if "```" in s or "<code-ref" in s or "<code id" in s:
            raise ValueError(
                "intro must NOT contain code blocks/envelopes — pure prose"
            )
        return s

    @field_validator("subtopics")
    @classmethod
    def _validate_subtopics(cls, v: list[Subtopic]) -> list[Subtopic]:
        if not (_SUBTOPICS_MIN <= len(v) <= _SUBTOPICS_MAX):
            raise ValueError(
                f"subtopics count must be {_SUBTOPICS_MIN}-"
                f"{_SUBTOPICS_MAX}; got {len(v)}"
            )
        # Empty hashes (prose subtopics) are exempt from the uniqueness
        # check — a prose-only section legitimately has several "" hashes.
        hashes = [s.code_ref_hash for s in v if s.code_ref_hash]
        if len(set(hashes)) != len(hashes):
            raise ValueError(
                f"duplicate code_ref_hash across subtopics: {hashes}"
            )
        # Subheadings should be distinct within a section.
        subheads = [s.subheading.casefold() for s in v]
        if len(set(subheads)) != len(subheads):
            raise ValueError(
                f"duplicate subheading across subtopics: {[s.subheading for s in v]}"
            )
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
    """Persisted section (v2 cookbook). LLM fields from _LLMSectionDraft +
    node-injected observability/meta."""
    section_id:        str
    heading:           str
    intro:             str
    subtopics:         list[Subtopic]
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
    total_subtopics:      int
    total_citations:      int
    avg_subtopics_per_section: float
    avg_explanation_words: float


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
