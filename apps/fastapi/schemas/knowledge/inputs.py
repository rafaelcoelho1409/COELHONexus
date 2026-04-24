"""
Knowledge Distiller — Request DTOs (user-facing)

Pydantic models for FastAPI request bodies. Pydantic validates every
field at request time; bad inputs return HTTP 422 before the handler
runs (the same defensive pattern we applied in schemas/youtube/inputs.py).

See docs/KNOWLEDGE-DISTILLER-ARCHITECTURE.md for the canonical shape.
"""
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, StringConstraints


# Reusable: strips surrounding whitespace, requires ≥1 char.
# Rejects "", "   ", "\n\t" with a 422 before the handler runs.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


# =============================================================================
# User Profile — drives tone adaptation at synthesis time
# =============================================================================
class UserProfile(BaseModel):
    """
    Learning profile. Coverage is CONSTANT across levels — only presentation
    varies (code density, assumption level, example depth).

    See docs/KNOWLEDGE-DISTILLER-WHOLE-DOCS-VARIABLE-TONE.md for the principle.
    """
    level: Literal["junior", "mid", "senior"] = "senior"
    target_markets: list[NonEmptyStr] = Field(
        default_factory = list,
        description = "Geo markets the user targets. Example: ['uae', 'singapore']"
    )
    mastered_technologies: list[NonEmptyStr] = Field(
        default_factory = list,
        description = "Tech the user already knows — synthesizer will skip intros and focus on novel aspects"
    )
    portfolio_refs: list[NonEmptyStr] = Field(
        default_factory = list,
        description = "User's flagship projects to cross-reference in examples. Example: ['COELHO RealTime', 'COELHO Agents']"
    )
    acceptance_threshold: float = Field(
        default = 0.85, 
        ge = 0.0, 
        le = 1.0,
        description = "Minimum grader composite score before a chapter is accepted (0.0-1.0)"
    )


# =============================================================================
# Study Creation — POST /api/v1/knowledge/studies
# =============================================================================
class CreateStudyRequest(BaseModel):
    """
    Kick off a new study. Two-step flow:
      1. Call POST /studies/resolve first to get a proposed docs_url from
         search + LLM disambiguation. Returns the resolved URL WITHOUT
         enqueueing anything.
      2. Pass that URL (or your own) as `docs_url` to POST /studies. The
         handler HEAD-verifies reachability, runs the scope gate, then
         enqueues the Celery task.

    This makes the "wrong URL crawled" failure mode a 3-second confirmation
    step instead of a 10-15 min wasted crawl.

    Storage: all artifacts land in MinIO under key prefix
    `{user_id}/knowledge/{framework}-{version}-{ts}/...`
    """
    framework: NonEmptyStr = Field(
        description = "Code framework, library, SDK, CLI tool, or developer topic. Examples: 'FastAPI', 'React', 'CUDA', 'tokio'"
    )
    version: Optional[NonEmptyStr] = Field(
        default = None,
        description = "Optional version pin. None = latest. Example: '0.104.1'"
    )
    docs_url: NonEmptyStr = Field(
        description = (
            "REQUIRED. Official documentation root URL. Must be reachable "
            "(HEAD-verified). Use POST /studies/resolve first to have the "
            "system propose one based on framework name + search, "
            "or paste the URL directly if you already know it."
        )
    )
    user_id: NonEmptyStr = Field(
        default = "default",
        description = "Multi-tenancy key. Used as the top-level MinIO prefix. When JWT auth lands, the router overrides this with the authenticated user's ID."
    )
    user_profile: UserProfile = Field(default_factory = UserProfile)

    # -----------------------------------------------------------------
    # Resolver-provided hints (optional) — forwarded to the ingestion
    # dispatcher so it can pick the right tier strategy instead of
    # defaulting to Playwright for everything. These come directly from
    # `POST /studies/resolve` → `ResolvedDocs`. Omit when calling /studies
    # directly with just a docs_url (legacy path); the dispatcher will
    # fall through to Tier 4 (Crawl4AI Playwright).
    # -----------------------------------------------------------------
    tier: Optional[Literal[1, 2, 3, 4]] = Field(
        default = None,
        description = (
            "Resolver-classified ingestion tier (1-4). Controls which "
            "strategy the ingestion pipeline runs: 1=llms-full.txt fast "
            "path, 2=llms.txt parallel fetch, 3=sitemap.xml httpx, "
            "4=Crawl4AI Playwright. None = fall through to Tier 4."
        ),
    )
    repo_url: Optional[NonEmptyStr] = Field(
        default = None,
        description = "Resolver-provided source repo URL (github.com/org/repo), used by Tier-GH."
    )
    github_discover: Optional[Literal["homepage", "pages", "readme_only", "api_unavailable", "no_repo_in_path"]] = Field(
        default = None,
        description = (
            "Outcome of the resolver's GitHub repo discovery (when docs_url "
            "landed on github.com). 'readme_only' triggers the Tier-GH branch "
            "that fetches raw *.md via the GitHub API instead of Playwright-"
            "crawling the file tree page."
        ),
    )
    github_org: Optional[NonEmptyStr] = Field(
        default = None,
        description = "GitHub org (from `repo_url` / resolver discovery). Used by Tier-GH."
    )
    github_repo: Optional[NonEmptyStr] = Field(
        default = None,
        description = "GitHub repo name. Used by Tier-GH."
    )
    github_default_branch: Optional[NonEmptyStr] = Field(
        default = None,
        description = "Default branch reported by the GitHub API. Used by Tier-GH to build raw.githubusercontent.com URLs."
    )
    preview: bool = Field(
        default = False,
        description = (
            "Tier 4 #16 (2026-04-24): when True, run the CLASSICAL-ONLY preview "
            "pipeline — ingest → classical clustering (embed + k-means) → "
            "c-TF-IDF cluster labels → TextRank extractive summaries. NO LLM "
            "calls at any stage. Produces a ~5-min sketch with preview.md + "
            "per-chapter extractive READMEs. Useful as (a) a sanity-check "
            "before committing to the full ~30 min synthesis run, (b) a "
            "fallback when all LLM providers are down, (c) a verbatim-by-"
            "construction baseline for validating synth outputs."
        ),
    )


# =============================================================================
# Batch Creation — POST /api/v1/knowledge/studies/batch
# =============================================================================
# Consumes the `studies[]` output of `/studies/resolve` directly. The batch
# endpoint is the post-resolver orchestrator — it does NOT re-run resolution
# or re-run coalescing. Each ResolvedStudy in the payload becomes one Celery
# task in a `chain(...)` pipeline, guaranteeing strict sequential execution
# across the batch (no LLM rate-limit thrashing, no target-host 429 bursts).
#
# Typical flow:
#   1. Client POSTs a ResolveRequest to /studies/resolve.
#   2. Client reviews the returned `studies[]` (may split / edit groups).
#   3. Client POSTs the (possibly edited) `studies[]` here.
#   4. Server enqueues one Celery task per member of `studies[]`, linked
#      via chain() for serial execution. Returns a batch_id + per-study
#      study_ids so the frontend can poll GET /studies/batch/{batch_id}.
from schemas.knowledge.resolver import ResolvedStudy


class CreateBatchRequest(BaseModel):
    """
    Batch study creation. Accepts the coalesced `studies[]` list from
    `/studies/resolve`. Each ResolvedStudy becomes exactly one Celery task
    in the chain; a coalesced group (coalesced_from ≥ 2) materializes as
    ONE unified study whose MinIO prefix and manifest carry all member
    canonical_names.
    """
    studies: list[ResolvedStudy] = Field(
        min_length = 1,
        max_length = 8,
        description = (
            "Coalesced studies from /studies/resolve. Cap at 8 entries to "
            "prevent runaway batches; users wanting more should split into "
            "explicit follow-up requests."
        ),
    )
    user_id: NonEmptyStr = Field(
        default = "default",
        description = "Multi-tenancy key — MinIO top-level prefix. When JWT auth lands, the router overrides from the authenticated session.",
    )
    user_profile: UserProfile = Field(default_factory = UserProfile)
    max_concurrent_chapters: int = Field(
        default = 5,
        ge = 1,
        le = 10,
        description = (
            "Per-study inner-chapter synthesis concurrency. This applies WITHIN "
            "each chained study; the chain itself remains strictly sequential "
            "across studies regardless of this value."
        ),
    )


# =============================================================================
# Export — POST /api/v1/knowledge/studies/{id}/export
# =============================================================================
ExportFormat = Literal["pdf", "html", "epub", "anki"]


class ExportRequest(BaseModel):
    """Generate a derived artifact from the canonical markdown. Triggers a Celery task."""
    format: ExportFormat
