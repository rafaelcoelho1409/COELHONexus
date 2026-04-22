"""
Knowledge Distiller — LangGraph State

LangGraph state is a TypedDict, not a Pydantic model. The reducer mechanism
(Annotated[..., reducer]) requires TypedDict keys — Pydantic doesn't play
well with reducers. Pydantic models live *inside* state fields when we need
validated structured data (see user_profile, plan).

Key reducer: `synthesis_results` uses `operator.add` because N parallel
Send() workers each return `{"synthesis_results": [one_entry]}`; without
the reducer each write would overwrite the previous.

Canonical arch: docs/KNOWLEDGE-DISTILLER-ARCHITECTURE.md
"""
import operator
from typing import Annotated, Literal, Optional, TypedDict

from schemas.knowledge.inputs import UserProfile
from schemas.knowledge.agents import ChapterPlan


# =============================================================================
# Phase & Tier type aliases
# =============================================================================
Phase = Literal[
    "scope",        # Scope-gate pre-flight (sync, before Celery enqueue)
    "ingest",       # Tiered extractor writes research/raw/*.md
    "plan",         # Planner decomposes corpus into 4-12 chapters
    "synthesize",   # Parallel Send() workers: synth+grade Self-Refine loop
    "critic",       # RAGAS-style verification
    "assemble",     # summary.md + DEBT.md + episodic memory update
    "complete",
    "failed",
]

IngestTier = Literal[
    "llms_full_txt",        # Tier 1 — single HTTP GET of /llms-full.txt
    "llms_txt",             # Tier 2 — parse llms.txt + parallel .md fetch
    "sitemap",              # Tier 3 — standard web sitemap.xml
    "crawl4ai",             # Tier 4 — BFS deep crawl with keyword scorer
    "github_readme_only",   # Tier-GH — GitHub tree API + raw.githubusercontent.com
    "none",                 # Not yet attempted
]


# =============================================================================
# Per-chapter result — what each Send() worker accumulates into the state
# =============================================================================
class ChapterResult(TypedDict):
    """One chapter's final outputs. N of these accumulate into synthesis_results."""
    number: int
    score: float            # Final weighted_score after any Self-Refine iterations
    iterations: int         # 1 = accepted first try; 2 or 3 = one/two refinement rounds
    content_path: str       # studies/<root>/chapterNN/README.md
    challenges_path: str    # studies/<root>/chapterNN/challenges.md — active-recall questions
    flashcards_path: str    # studies/<root>/chapterNN/flashcards.json — Anki-importable Q/A


# =============================================================================
# Root state — flows between every LangGraph node
# =============================================================================
class KnowledgeDistillerState(TypedDict):
    """
    Root state for the Knowledge Distiller compiler pipeline.
    Persisted via AsyncPostgresSaver after every node so runs survive restarts.
    """
    # -- Input (set at dispatch time) --
    study_id: Optional[str]         # UUID of this study — threads into DocsIngestionConfig for IngestProgress → Redis (used by /stream SSE). None on legacy graph invocations.
    framework: str
    version: Optional[str]
    docs_url: Optional[str]
    # Coalesced-group fields. For solo studies, len == 1 and the values mirror
    # `framework` / `docs_url`. For studies from POST /studies/batch with a
    # coalesced ResolvedStudy (coalesced_from ≥ 2), these carry the full
    # member list so the ingester (once updated) can union subtree prefixes
    # for Tier 2/3/4 fetches. Tier 1 reads only `docs_url` — the monolithic
    # llms-full.txt is source-of-truth regardless.
    docs_urls: list[str]            # [docs_url] for solo; [...N...] for coalesced groups
    canonical_names: list[str]      # [framework] for solo; [name, name, ...] for coalesced groups
    language: Optional[str]         # Programming language scope (from ScopeValidation.language at Step 11)
    user_id: str                    # MinIO multi-tenancy key (from CreateStudyRequest.user_id)
    user_profile: UserProfile       # Pydantic model stored inside TypedDict (JSON-serializable)
    study_root: str                 # MinIO object key prefix: "{user_id}/knowledge/{framework}-{version}-{ts}"
    # -- Resolver hints (set at dispatch time, optional — absent on legacy --
    # callers that POST to /studies without going through /resolve first). --
    # The ingest_docs node forwards these into DocsIngestionConfig so the  --
    # dispatcher picks Tier 1/2/3/4 or Tier-GH without re-probing.         --
    tier: Optional[int]             # 1, 2, 3, 4 or None (→ Tier 4 default)
    github_discover: Optional[str]  # "homepage" | "pages" | "readme_only" | None
    github_org: Optional[str]
    github_repo: Optional[str]
    github_default_branch: Optional[str]
    repo_url: Optional[str]
    # -- Phase tracking --
    current_phase: Phase
    ingest_tier_used: IngestTier
    # -- Ingest outputs --
    raw_files: list[str]            # File slugs written to research/raw/
    manifest: list[dict]            # [{url, slug, tier, bytes, fetched_at}, ...]
    # -- Plan outputs --
    plan: list[ChapterPlan]         # 4-12 ChapterPlan Pydantic models
    # -- Synth outputs — accumulated from N parallel Send() workers --
    synthesis_results: Annotated[list[ChapterResult], operator.add]
    # -- Critic outputs --
    validation_report: Optional[dict]   # Serialized CriticAssessment
    # -- Assemble outputs --
    summary_path: Optional[str]     # studies/<root>/summary.md
    debt_path: Optional[str]        # studies/<root>/DEBT.md
