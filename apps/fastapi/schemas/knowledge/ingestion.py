"""
Knowledge Distiller — Docs Ingestion Schemas

Pydantic models consumed by services/knowledge/ingestion.py. Required
strings use NonEmptyStr for 422-fail-fast validation, matching the
rest of the KD schema layer.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field

from schemas.knowledge.inputs import NonEmptyStr


# =============================================================================
# Type aliases
# =============================================================================
# Which tier won the ingestion race. Mirrors IngestTier in state.py but
# excludes "none" — ManifestEntry and IngestResult only exist after a tier
# actually succeeded.
IngestTierName = Literal["sitemap", "crawl4ai"]


# =============================================================================
# Config + Result models
# =============================================================================
class DocsIngestionConfig(BaseModel):
    """Per-run tunables for the ingestion waterfall."""
    framework: NonEmptyStr
    version: Optional[NonEmptyStr] = None    # from state; passed to cache for versioned storage. None/"latest" share the "latest" cache bucket
    docs_url: NonEmptyStr                    # must be provided; router resolves if user didn't supply
    language: Optional[NonEmptyStr] = None   # from ScopeValidation.language — drives filtering
    study_root: NonEmptyStr                  # MinIO object key prefix; raw files land at <study_root>/research/raw/<slug>.md
    max_pages: int = Field(default = 10_000, ge = 10, le = 50_000)  # effectively "no cap" for sitemaps; Tier 4 BFS still respects it
    max_depth: int = Field(default = 5, ge = 1, le = 10)           # Tier 4 depth
    http_timeout: int = Field(default = 30, ge = 5, le = 120)      # per-request timeout (s)
    concurrent_fetches: int = Field(default = 8, ge = 1, le = 20)  # Tier 2/3 parallelism (raised 5→8 for uncapped sitemaps)
    min_page_chars: int = Field(default = 150, ge = 50, le = 5_000)  # was 400 (2026-04-21): PruningContentFilter now aggressively strips nav/sidebar chrome BEFORE this check, so "raw+chrome" is no longer the input — it's the already-pruned fit_markdown. 150 post-prune catches genuine stubs (50-100 chars) without killing thin-but-valid utility function pages (~200-400 chars of real content)
    max_link_text_ratio: float = Field(default = 0.55, ge = 0.1, le = 1.0)  # post-filter: drop pages that are >N% anchor text (navigation/index pages)
    extra_allow_patterns: list[NonEmptyStr] = Field(default_factory = list)
    extra_deny_patterns: list[NonEmptyStr] = Field(default_factory = list)


class ManifestEntry(BaseModel):
    """One row per ingested file."""
    url: NonEmptyStr
    slug: NonEmptyStr
    tier: IngestTierName
    bytes: int = Field(ge = 0)


class IngestResult(BaseModel):
    """Aggregate returned to caller. Only constructed after a tier succeeds."""
    tier_used: IngestTierName
    total_files: int = Field(ge = 0)
    total_bytes: int = Field(ge = 0)
    manifest: list[ManifestEntry]
    skipped_urls: list[NonEmptyStr] = Field(default_factory = list)