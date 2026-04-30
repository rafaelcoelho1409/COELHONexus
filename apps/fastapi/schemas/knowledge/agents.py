"""
Knowledge Distiller — LLM Structured Output Schemas

These Pydantic models are passed to `llm.with_structured_output(Model)`.
The LLM reads each field's `description=...` to understand what to produce —
so descriptions are prompts, not documentation.

Every call in the KD pipeline that extracts structured data from an LLM has
a corresponding model here: planner, grader, critic.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


# =============================================================================
# Planner — decomposes the ingested corpus into chapters
# =============================================================================
class ChapterPlan(BaseModel):
    """One chapter in the plan. Planner emits N of these (4 ≤ N ≤ 12)."""
    number: int = Field(
        ge = 1, 
        le = 12,
        description = "1-indexed chapter number in reading order"
    )
    title: str = Field(
        description = "Concise chapter title. Example: 'Request Handling & Dependency Injection'"
    )
    goal: str = Field(
        description = "One-sentence statement of what the reader gains from this chapter"
    )
    assigned_files: list[str] = Field(
        min_length = 1,
        description = "Slugs of files under research/raw/ this chapter synthesizes from. Each file must be assigned to at most ONE chapter across the whole plan."
    )


class UnusedFile(BaseModel):
    """A file from the corpus the planner judged not worth synthesizing into any chapter."""
    slug: str = Field(
        description = "The slug exactly as it appeared in the corpus summary."
    )
    reason: str = Field(
        description = (
            "Short explanation (<= 80 chars) of why this file adds nothing to the study. "
            "Examples: 'auto-generated API stub with no prose', "
            "'release notes listing internal tickets', "
            "'navigation page with only a table of contents', "
            "'duplicate of existing quickstart'."
        )
    )


# =============================================================================
# Map-Reduce Planner — shard-level micro-clusters (2026-04-21 research)
# =============================================================================
# The planner uses a two-pass map-reduce shape (docs/KNOWLEDGE-DISTILLER-
# PLANNER-FIXES.md §Fix #1):
#   1. MAP: shard corpus into chunks of ≤40 files. Each shard LLM call
#      returns 1-3 micro-clusters (topic label + file assignment).
#   2. REDUCE: a second LLM call merges N shard results into 4-12
#      chapters. Small prompts in both passes keep every call under
#      Groq's 12K TPM free-tier budget.
class ShardCluster(BaseModel):
    """
    One micro-cluster within a single shard — a topical grouping of files
    from that shard. The reducer merges similar clusters across shards
    into final chapters.
    """
    cluster_name: str = Field(
        description = (
            "Short topic label identifying this micro-cluster. 2-6 words. "
            "Example: 'CLI Agent Runtime', 'Filesystem Middleware', "
            "'Subagent Orchestration'. Keep consistent terminology across "
            "shards so the reducer can merge similar clusters."
        ),
    )
    description: str = Field(
        description = (
            "One-sentence description of what this micro-cluster covers. "
            "≤150 chars. Helps the reducer decide merges."
        ),
    )
    file_slugs: list[str] = Field(
        min_length = 1,
        description = (
            "Slugs of files from THIS SHARD that belong in this cluster. "
            "Must be drawn from the shard's input slug list."
        ),
    )


class ShardLabels(BaseModel):
    """Output of the shard-labeler (MAP pass). 1-3 clusters per shard typical."""
    clusters: list[ShardCluster] = Field(
        min_length = 1,
        max_length = 5,
        description = "Micro-clusters this shard's files group into. 1-3 typical; 5 hard cap.",
    )
    unused_shard_slugs: list[str] = Field(
        default_factory = list,
        description = (
            "Slugs from THIS SHARD that are low-value noise (release notes, "
            "auto-generated stubs, navigation pages). Reducer propagates these "
            "to the final plan's unused_files bucket."
        ),
    )


class ChapterPlanList(BaseModel):
    """
    Planner's output — the full chapter structure.
    N is DYNAMIC: small frameworks get 4 chapters, deep ones get up to 12.
    LangGraph Send() fan-out uses N to spawn parallel synthesizer workers.
    """
    chapters: list[ChapterPlan] = Field(
        min_length = 4,
        max_length = 12,
        description = "Ordered chapter plan. 4 to 12 chapters; every USED file assigned to exactly one chapter."
    )
    unused_files: list[UnusedFile] = Field(
        default_factory = list,
        description = (
            "Files from the corpus that DON'T belong in the study. Use this to "
            "explicitly discard noise: release notes, auto-generated API stubs, "
            "navigation pages, trivial redirects, etc. Prefer dropping a file "
            "over forcing it into a chapter it doesn't fit. Target: <20% of the "
            "corpus dropped (higher rates may indicate an ingestion problem)."
        )
    )
    reasoning: str = Field(
        description = "Brief justification for the chapter count, grouping, and any notable drops."
    )


# =============================================================================
# Clio-pattern REDUCE — per-meta-cluster labeling + global ordering
# =============================================================================
# Used by graphs/knowledge/reduce_cluster.py to replace the single-shot
# CHAPTER_REDUCE_PROMPT call for large corpora. See that module's docstring
# for the end-to-end architecture.
class MetaLabelDraft(BaseModel):
    """
    Output of one META_LABEL_PROMPT call. The LLM emits title + goal for ONE
    meta-cluster; assigned_files is filled in deterministically by the
    reducer (union of member micro-clusters' file_slugs), and `number` is
    assigned by the ORDER_PROMPT pass. Keeping this schema minimal lets the
    labeling call stay under ~500 output tokens regardless of meta-cluster size.
    """
    title: str = Field(
        description = (
            "Concise chapter title — 2-6 words. Covers the intersection of "
            "the input micro-clusters' topics. Avoid generic titles like "
            "'Overview' or 'Miscellaneous'."
        ),
    )
    goal: str = Field(
        description = (
            "One sentence, ≤200 chars. What the reader GAINS from the chapter "
            "(not what's in it). Starts with a verb: 'Understand', 'Learn to', "
            "'Build'."
        ),
    )


class OrderedIndices(BaseModel):
    """
    Output of the ORDER_PROMPT pass. The LLM sees M chapter drafts and
    returns a permutation of 0..M-1 representing the reading order.

    The reducer validates: len == M, set(order) == set(range(M)). No
    min/max_length is set on the field because M varies per run — we
    enforce correctness client-side.
    """
    order: list[int] = Field(
        description = (
            "Permutation of 0..M-1 indicating reading order. "
            "order[0] is the first chapter the reader should read. "
            "Must contain every index exactly once. No repeats, no holes."
        ),
    )
    rationale: str = Field(
        description = (
            "One sentence explaining the spine of the ordering "
            "(e.g., 'Foundations first, then runtime integrations, "
            "then advanced orchestration')."
        ),
    )


# =============================================================================
# Adaptive Grader — 8-dimensional evaluation (inside the Self-Refine loop)
# =============================================================================
class Issue(BaseModel):
    """
    Span-anchored issue flagged by the grader.

    Research (CRITIC, Gou et al. 2023 — arxiv 2305.11738 §3): natural-language
    feedback grounded in *specific spans* of the output beats free-form
    critique lists. A generic issue like "missing examples" tells the
    refiner to add examples ANYWHERE, and the LLM often over-corrects
    globally (rewriting the whole chapter). A span-anchored issue tells the
    refiner WHERE to edit — a surgical change that preserves the rest.
    """
    span_quote: str = Field(
        description = (
            "Exact text span from the chapter that has the issue. Quote "
            "verbatim (10-200 chars). Example: 'from fastapi import FastAPI'. "
            "The refiner uses this to find the location to edit."
        ),
    )
    dimension: str = Field(
        description = (
            "Which of the 8 rubric dimensions this issue hurts. One of: "
            "signal_to_noise, assumption_match, job_alignment, citation_integrity, "
            "code_density, portfolio_synergy, complexity_appropriate, "
            "market_analysis."
        ),
    )
    suggestion: str = Field(
        description = (
            "Specific edit to apply to the quoted span only. Example: "
            "'Add `# docs: quickstart.md` comment above this line.' Keep "
            "≤120 chars. Don't tell the refiner to change other parts — "
            "narrow edits only."
        ),
    )


class GraderEvaluation(BaseModel):
    """
    Per-chapter grade. Dimensions score presentation (not coverage — coverage
    is enforced by the planner's file assignments).

    Reference: docs/STUDY-GENERATOR-ADAPTIVE-GRADER.md
    """
    signal_to_noise: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "How code-first and padding-free is the chapter? 1.0 = every section starts with code, no 'In this chapter we will...' intros"
    )
    assumption_match: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Are assumed-known topics appropriate for user's mastered_technologies? 1.0 = skips what user knows, explains only what's genuinely novel"
    )
    job_alignment: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Does content reference user's target_markets (e.g., UAE G42, Singapore DBS)? 1.0 = concrete, realistic market hooks"
    )
    citation_integrity: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Does every non-trivial claim have a '# docs:' citation back to research/raw/*? 1.0 = full traceability"
    )
    code_density: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Fraction of non-blank lines that are code. Target: 0.7+ for senior, 0.4+ for junior"
    )
    portfolio_synergy: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Does content link to user's portfolio_refs projects? 1.0 = explicit cross-references"
    )
    complexity_appropriate: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Material depth matches the framework's conceptual load? 1.0 = right theory/API ratio"
    )
    market_analysis: float = Field(
        ge = 0.0,
        le = 1.0,
        description = "Money-project suggestions realistically monetizable in target_markets? 1.0 = actionable"
    )
    code_preservation_ratio: float = Field(
        ge = 0.0,
        le = 1.0,
        default = 1.0,
        description = (
            "Tier 2 #19 (2026-04-23): deterministic score computed upstream "
            "on the assembled chapter. 1.0 = every vault hash appears "
            "exactly once in the output, distributed logically across "
            "sections (no duplicates, no orphans). 0.5 = some hashes "
            "appear multiple times OR some sections have no code despite "
            "substantive prose. 0.0 = mass duplication or missing blocks. "
            "Runs alongside the audit — if audit passes, this is ≥0.9 by "
            "construction; lower only if distribution is uneven. Carries "
            "2× weight in the composite, same as signal_to_noise + "
            "citation_integrity."
        ),
    )
    weighted_score: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Composite 0.0-1.0. Accept if >= user_profile.acceptance_threshold (default 0.85)"
    )
    specific_issues: list[Issue] = Field(
        default_factory = list,
        description = (
            "Span-anchored issues the refiner should address on the next "
            "iteration. Each item is a (span_quote, dimension, suggestion) "
            "tuple — the refiner finds the quote in the chapter and applies "
            "ONLY the suggested edit to that span. CRITIC pattern "
            "(arxiv 2305.11738) — span anchoring prevents over-correction."
        ),
    )
    action: Literal["accept", "refine", "regenerate"] = Field(
        description = "accept = weighted_score met threshold; refine = retry with targeted adjustments; regenerate = start over (major structural issue)"
    )


# =============================================================================
# Synthesizer — one chapter's 3 artifacts, produced in a single LLM call
# =============================================================================
class Flashcard(BaseModel):
    """Anki-style Q/A pair for flashcards.json."""
    front: str = Field(
        description = "Question side — concise prompt that stands alone without the back. Example: 'What does FastAPI Depends() enable?'"
    )
    back: str = Field(
        description = "Answer side — precise, self-contained. No 'see chapter X' style references."
    )


class ChapterSynthesis(BaseModel):
    """
    LEGACY synthesizer output schema (free-form markdown). Kept for the
    assembler's input contract: after the synth node runs with Tier 3 #21's
    structured output, we build a ChapterSynthesis from the assembled
    markdown so downstream (grader / critic / artifact writer / curator) see
    their existing shape.

    Do NOT pass this schema to `with_structured_output` — use
    ChapterOutput instead (Tier 3 #21, 2026-04-23). Free-form markdown
    let the LLM strip code blocks bimodally across models (ch03/04/05/08
    hit 0-21% preservation on the 2026-04-23 smoke test). ChapterOutput
    removes the strip path by construction.
    """
    content: str = Field(
        description = (
            "Full chapter markdown (ASSEMBLED from ChapterOutput + code vault)."
        )
    )
    challenges: str = Field(
        description = (
            "5-10 active-recall questions as a markdown numbered list."
        )
    )
    flashcards: list[Flashcard] = Field(
        min_length = 4,
        max_length = 15,
        description = "4-15 Anki-style Q/A pairs. Each pair stands alone. (2026-04-24: relaxed from min=8 to min=4 after Run-8 ch01 sentinel'd because the LLM produced 4 flashcards; deterministic validator rejected it pre-Self-Refine. 4 is still meaningful flashcard coverage.)"
    )


# =============================================================================
# Tier 3 #21 — Structured-output synthesizer (2026-04-23)
# =============================================================================
# Replaces the free-form `ChapterSynthesis.content` field with a structured
# list of sections. Each section names which vault-hashes ("code_refs") get
# interleaved after its prose. The assembler builds the final markdown
# deterministically — the LLM never emits code itself, so it cannot strip,
# paraphrase, reformat, or invent fenced code blocks. prose_md is free-form
# markdown; the audit reports ``` fence presence as a soft-violation to the
# refine loop, but the schema doesn't enforce it via Pydantic validation
# (because Pydantic rejection cascades the whole fallback chain before the
# LLM can see targeted feedback).
#
# Escalation trigger: 2026-04-23 smoke (Run 3) showed 4 of 8 reporting
# chapters at ≥50% iter-0 strip under Tier 0d-3/0d-4 prompts. Roadmap
# (KNOWLEDGE-DISTILLER-IMPROVEMENTS-ROADMAP.md line 388-396) pre-authorizes
# escalation to this architecture under that condition.
class Section(BaseModel):
    """
    One section of a chapter: a heading, explanatory prose, and an ordered
    list of code-block hashes (from the Tier 0a vault) to emit AFTER the
    prose in the assembled output.
    """
    heading: str = Field(
        description = (
            "Section heading WITHOUT leading '#' markers — the assembler adds "
            "the right heading level. 2-8 words. Example: 'Async Client', "
            "'Dependency Injection'. Avoid 'Introduction', 'Overview', "
            "'Summary', 'Conclusion'."
        ),
    )
    prose_md: str = Field(
        description = (
            "Section body as markdown. RULES:\n"
            " 1. NO triple-backtick (```) fenced code blocks — put code in "
            "`code_refs`, the assembler interleaves it.\n"
            " 2. NO <code-ref hash=\"...\"/> XML tags — copy the 12-hex hash "
            "value INTO `code_refs` instead.\n"
            " 3. Include `# docs: <file_slug>` citations for every non-"
            "trivial claim — as bare lines in prose; the assembler preserves "
            "them verbatim.\n"
            " 4. Inline `code` spans (single backtick) are fine.\n"
            " 5. Dense, production-focused, code-first phrasing."
        ),
    )
    code_refs: list[str] = Field(
        default_factory = list,
        description = (
            "Ordered list of 12-hex-char vault hashes. Take each hash from "
            "the input's `<code-ref hash=\"<12-hex>\"/>` tags — use only the "
            "bare 12-char value (no `lf_`/`<`/`\"` wrappers). The assembler "
            "emits each referenced fenced code block AFTER this section's "
            "prose_md in the order you list them. Every vault hash that "
            "conceptually belongs with this section MUST appear here; missing "
            "hashes fail the preservation audit and force a refine retry."
        ),
    )


class ChapterOutput(BaseModel):
    """
    Structured synthesizer output — replaces ChapterSynthesis in
    `with_structured_output(ChapterOutput)` calls. Assembler converts to
    markdown for downstream (grader / critic / curator / artifact writer).
    """
    sections: list[Section] = Field(
        min_length = 1,
        description = (
            "Ordered list of chapter sections. First section's heading becomes "
            "the top content under the chapter's H1 title; subsequent sections "
            "become H2s."
        ),
    )
    challenges: str = Field(
        description = (
            "5-10 active-recall questions as a markdown numbered list. Mix of "
            "conceptual ('Why does X block on Y?') and applied ('Write a "
            "function that does Z using this framework')."
        ),
    )
    flashcards: list[Flashcard] = Field(
        min_length = 4,
        max_length = 15,
        description = "4-15 Anki-style Q/A pairs. Each pair stands alone. (2026-04-24: relaxed from min=8 to min=4 after Run-8 ch01 sentinel'd because the LLM produced 4 flashcards; deterministic validator rejected it pre-Self-Refine. 4 is still meaningful flashcard coverage.)"
    )


# =============================================================================
# OP-46 (2026-04-25, post-Run-12) — prose-only chapter output
# =============================================================================
# Variant of ChapterOutput for chapters whose source files contain ZERO
# fenced code blocks (security policies, compliance docs, design philosophy,
# best-practices narratives). Run-12 evidence: 7/7 Docker chapters had
# code_vault={} → ChapterOutput's code_refs constraint forced the LLM to
# either return None (cascade exhaustion) or hallucinate hashes. Prose-only
# path skips code_refs entirely; audit becomes citation + length checks.
class ProseSection(BaseModel):
    """Section variant for chapters with no fenced code blocks. Prose only."""
    heading: str = Field(
        description = (
            "Section heading WITHOUT leading '#' markers — the assembler adds "
            "the right heading level. 2-8 words. Avoid 'Introduction', "
            "'Overview', 'Summary', 'Conclusion'."
        ),
    )
    prose_md: str = Field(
        description = (
            "Section body as markdown. RULES:\n"
            " 1. NO triple-backtick (```) fenced code blocks — this chapter's "
            "source had none, so the synthesizer must not invent any. Use "
            "inline `code` spans (single backtick) for short identifiers.\n"
            " 2. Include `# docs: <file_slug>` citations on their own lines for "
            "every non-trivial claim. The assembler preserves them verbatim.\n"
            " 3. Dense, production-focused phrasing. Concrete > abstract."
        ),
    )


class ProseChapterOutput(BaseModel):
    """
    Prose-only structured output for chapters whose vault is empty.
    No code_refs field; no hash placement audit. Returned by
    `_synthesize_attempt` when `len(code_vault) == 0`.
    """
    sections: list[ProseSection] = Field(
        min_length = 1,
        description = (
            "Ordered list of chapter sections. First section's heading becomes "
            "the top content under the chapter's H1 title; subsequent sections "
            "become H2s."
        ),
    )
    challenges: str = Field(
        description = (
            "5-10 active-recall questions as a markdown numbered list. Mix of "
            "conceptual and applied (where applicable to a code-free domain)."
        ),
    )
    flashcards: list[Flashcard] = Field(
        min_length = 4,
        max_length = 15,
        description = "4-15 Anki-style Q/A pairs."
    )


# =============================================================================
# OP-HIERARCHICAL-SYNTH (2026-04-26, Round 2 post-Run-20) — outline schemas
# =============================================================================
# Phase A of the hierarchical synth pipeline. The outline LLM call has NO enum
# constraint (no code_refs field) — pure prose generation. This avoids the
# "constraint-vs-prose attention competition" that monolithic synth suffers
# (Chroma context-rot 2024; Brenndoerfer constrained-decoding analysis).
#
# A ChapterOutline pre-allocates 5-15 sections with cross-section contracts.
# Phase B (deterministic hash routing) then assigns vault hashes to each
# OutlineSection by source-file + topical proximity. Phase C synthesizes each
# section in parallel with a small per-section enum (assigned_hashes ∪
# shared_core, typically 8-15 values — well under the 30-distractor cliff).
# Phase D merges back into a regular ChapterOutput for the existing grader /
# critic / curator / assembler downstream pipeline (no schema changes there).
class OutlineSection(BaseModel):
    """
    One section's pre-allocation: heading + goal + cross-section contract.
    No prose body, no code_refs — those are filled by Phase C section synth.
    """
    heading: str = Field(
        description = (
            "Section heading WITHOUT leading '#' markers. 2-8 words, concrete, "
            "code-y. Examples: 'Async Client', 'Dependency Injection'. Avoid "
            "'Introduction', 'Overview', 'Summary', 'Conclusion'."
        ),
    )
    goal: str = Field(
        description = (
            "1-line description of what this section will teach. Used by "
            "Phase B (hash routing) to assign topically-relevant vault hashes "
            "and by Phase C (per-section synth) as the synthesis target."
        ),
    )
    assumes_from_prior_sections: str = Field(
        default = "",
        description = (
            "What concepts this section assumes the reader has already absorbed "
            "from PRIOR sections in this chapter. Empty string for the first "
            "section. Examples: 'reader knows the basic agent loop from "
            "section 1' or 'reader has seen the streaming response shape'. "
            "Used by Phase C to maintain cross-section coherence."
        ),
    )


class ChapterOutline(BaseModel):
    """
    Phase A output: chapter scaffold (sections + meta) before any code is
    placed. Generated by a single prose-only LLM call on the full chapter
    source. Cross-section contracts let Phase C parallel-synth each section
    independently while preserving narrative flow.

    The challenges + flashcards live HERE (not in Phase C) so they can be
    written holistically against the full source — they don't depend on any
    single section's prose decisions.
    """
    sections: list[OutlineSection] = Field(
        min_length = 4,
        max_length = 15,
        description = (
            "Ordered list of 4-15 sections. The outline gates fan-out on this "
            "count: too few = under-decomposed (Phase C wastes parallelism); "
            "too many = under-supported (each section has too few hashes to "
            "be coherent). 4-15 is the empirical sweet spot — matches the "
            "section counts that produced ACCEPTs in Run-13."
        ),
    )
    challenges: str = Field(
        description = (
            "5-10 active-recall questions as a markdown numbered list. Mix of "
            "conceptual ('Why does X block on Y?') and applied ('Write a "
            "function that does Z using this framework')."
        ),
    )
    flashcards: list[Flashcard] = Field(
        min_length = 4,
        max_length = 15,
        description = "4-15 Anki-style Q/A pairs. Each pair stands alone."
    )


# =============================================================================
# Critic — post-synthesis RAGAS-style verification (runs ONCE after all chapters)
# =============================================================================
class CriticAssessment(BaseModel):
    """
    Final quality check after every chapter is accepted by the grader.
    Focused on hallucinations and citation validity; distinct from the
    per-chapter grader's presentation-quality scoring.
    """
    citation_coverage: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Fraction of '# docs:' references that resolve to an existing file under research/raw/"
    )
    faithfulness: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Fraction of sampled factual claims verifiable against their cited source content (RAGAS faithfulness metric)"
    )
    code_syntax_valid: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Fraction of code blocks that parse/compile in their detected language (Python via ast, JS via AST parser, etc.)"
    )
    overall_score: float = Field(
        ge = 0.0, 
        le = 1.0,
        description = "Weighted composite of the three dimensions above. Study is 'healthy' if >= 0.85"
    )
    issues: list[str] = Field(
        default_factory = list,
        description = "Specific problems to write into DEBT.md. Example: 'chapter03:L47 cites missing research/raw/core-api.md'"
    )
