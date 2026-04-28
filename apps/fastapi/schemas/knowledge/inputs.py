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
    Kick off a new study. The framework name MUST exist in
    `apps/fastapi/files/sources.yaml` — the curated catalog is the single
    source of truth for docs URLs, tier routing, and repo metadata.

    Flow:
      1. (Optional) GET /api/v1/knowledge/resolve/sources to browse the catalog.
      2. POST /studies with {"framework": "<name>"}. The handler looks the
         name up via the resolver, derives docs_url + tier + repo metadata
         automatically, runs the scope gate, then enqueues the Celery task.

    Storage: all artifacts land in MinIO under key prefix
    `{user_id}/knowledge/{framework}-{version}-{level}`
    """
    framework: NonEmptyStr = Field(
        description = (
            "Catalog name from sources.yaml. Case-insensitive lookup. "
            "Examples: 'FastAPI', 'Docker', 'PyTorch'. Use "
            "GET /api/v1/knowledge/resolve/sources to list available names."
        )
    )
    version: Optional[NonEmptyStr] = Field(
        default = None,
        description = "Optional version pin. None = latest. Example: '0.104.1'"
    )
    user_id: NonEmptyStr = Field(
        default = "default",
        description = "Multi-tenancy key. Used as the top-level MinIO prefix. When JWT auth lands, the router overrides this with the authenticated user's ID."
    )
    user_profile: UserProfile = Field(default_factory = UserProfile)
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
    skip_below_threshold: bool = Field(
        default = True,
        description = (
            "OP-26 (2026-04-24): when True, BELOW-THRESHOLD best-effort "
            "chapters (committed with DEBT flag because Self-Refine exhausted "
            "without a passing graded iter) also write to the full cache "
            "with best_effort=true. Subsequent runs hit the cache and skip "
            "re-synthesizing them. Useful when you already have Run-N "
            "outputs you want to lock in, and only want to retry the "
            "sentinel'd chapters. OP-CACHE-DEBT-OUTPUT (2026-04-25, post-Run-16): "
            "default flipped from False → True because Run-16 produced 9 DEBT "
            "chapters that would all re-synth on a follow-up run (~2h burn) "
            "even though only ch03 (sentinel'd) actually needed regeneration. "
            "Flipping the default makes the common case (re-run after partial "
            "success) cheap by default; pass False explicitly when you want "
            "to bet the next iter lands a better output."
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
        default = 2,  # OP-20 (2026-04-24 late): was 5; aligned with graph default
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
