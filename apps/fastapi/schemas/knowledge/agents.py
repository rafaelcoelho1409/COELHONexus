"""
Knowledge Distiller — LLM Structured Output Schemas

These Pydantic models are passed to `llm.with_structured_output(Model)`.
The LLM reads each field's `description=...` to understand what to produce —
so descriptions are prompts, not documentation.

Every call in the KD pipeline that extracts structured data from an LLM has
a corresponding model here: scope gate, planner, grader, critic.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


# =============================================================================
# Scope Gate — rejects non-code-framework requests before any expensive work
# =============================================================================
class ScopeValidation(BaseModel):
    """
    Pre-flight classifier output. Runs on Groq llama-3.1-8b-instant (~500ms).
    See services/knowledge/scope.py in step 3.
    """
    is_code_framework: bool = Field(
        description = (
            "True ONLY if the input refers to a code/programming framework, library, "
            "SDK, API, CLI tool, or developer-focused technical topic — any programming "
            "language counts. Examples True: 'OpenTelemetry Python', 'React', 'CUDA', "
            "'Terraform', 'Rust tokio'. Examples False: 'how to bake a cake', 'stock "
            "market tips', 'yoga for beginners', 'marketing strategy'."
        )
    )
    detected_topic: str = Field(
        description = "Short label summarizing the subject. Example: 'FastAPI Python', 'Next.js', 'cake baking'"
    )
    language: Optional[str] = Field(
        default = None,
        description = "Primary programming language in scope if specified. Example: 'Python', 'Rust', 'TypeScript'"
    )
    docs_url: Optional[str] = Field(
        default = None,
        description = (
            "Official documentation root URL for the framework. Examples: "
            "'https://docs.pydantic.dev/latest', 'https://jinja.palletsprojects.com/en/stable', "
            "'https://fastapi.tiangolo.com', 'https://react.dev', 'https://docs.rs/tokio/latest'. "
            "Return the canonical docs root (not the project homepage). Null when is_code_framework=False."
        )
    )
    rejection_reason: Optional[str] = Field(
        default = None,
        description = "User-facing one-line explanation when is_code_framework=False. Empty string or null when True."
    )


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
    Synthesizer output for ONE chapter. Three artifacts produced in a single
    LLM call via with_structured_output(ChapterSynthesis), then written to
    MinIO as README.md, challenges.md, flashcards.json.
    """
    content: str = Field(
        description = (
            "Full chapter markdown. Starts every section with code (NO 'In this chapter...' "
            "intros). Every API call / feature mentioned gets '# docs: <file_slug>' citation. "
            "No 'Summary' or 'Conclusion' sections. Dense, code-first, production-focused."
        )
    )
    challenges: str = Field(
        description = (
            "5-10 active-recall questions as a markdown numbered list. Mix of conceptual "
            "('Why does X block on Y?') and applied ('Write a function that does Z using "
            "this framework')."
        )
    )
    flashcards: list[Flashcard] = Field(
        min_length = 8,
        max_length = 15,
        description = "8-15 Anki-style Q/A pairs. Each pair stands alone."
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
