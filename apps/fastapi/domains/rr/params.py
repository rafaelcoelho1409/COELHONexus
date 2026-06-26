"""Cross-cutting tunables for the RR domain (PURE-side, not agent).

Per docs/CODE-CONVENTIONS.md §3: frozen-dataclass groups of related
tunables. `SignalWeights` defines the radar's ranking-function shape;
per-profile overrides land in `radar_profiles.weights` (step 3+).

`DomainParams` carries the non-weight tunables that the scoring math
needs (recency half-life, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SignalWeights:
    """Weights for the composite `signal_score`. Sum need not equal 1 —
    the score is a sortable scalar, not a probability.

    Defaults tuned for LLMOps / agents / quantitative finance / applied
    math verticals. Per-profile overrides override these individually
    (the radar_profiles.weights JSONB is decoded into a SignalWeights
    with this dataclass's defaults as fallbacks for unspecified fields).
    """
    # Recency weight raised 0.15 → 0.20 to fix the
    # arxiv-only ranking degeneracy. When profile_embedding + citations +
    # HN/HF/S2 buzz all collapse to 0 (the common arxiv-only-source path),
    # the only differentiator left between an April-2025 paper and a 2018
    # paper was `vertical_fit`. Recency now carries enough weight to break
    # that tie cleanly.
    relevance:           float = 0.30   # cosine(profile_emb, paper_emb)
    recency:             float = 0.20   # half-life decay vs `now`
    citation_velocity:   float = 0.15   # citations / day_since_published
    influential_ratio:   float = 0.10   # S2 influential / total citations
    vertical_fit:        float = 0.15   # paper.categories ∩ profile.verticals
    cross_tier_buzz:     float = 0.10   # log1p(hn_points) + log1p(hf_upvotes)
    has_code:            float = 0.05   # PapersWithCode presence (v2 signal)
    # soft bias toward items with an arxiv_id when both
    # research papers and HN product posts are in the candidate pool.
    # Scan 28094718 had arxiv=0+hf=14+hn=5; with HF as the only paper
    # source the top-4 filled with 1 paper + 3 HN product announcements
    # (which can't be deep_read). This term gives arxiv-ID-bearing items
    # a small but decisive lift over no-ID items at otherwise comparable
    # signal levels. Weight intentionally small (0.08) so a strongly
    # buzzy HN post can still cross-rank a weakly-relevant arxiv paper.
    has_arxiv_id:        float = 0.08   # bias toward real papers vs HN posts


@dataclass(frozen=True, slots=True)
class DomainParams:
    """Non-weight constants used by the scoring helpers."""
    # Half-life raised 30 → 180 — 30 days was tuned for the
    # cross-tier buzz lifecycle (HN posts go stale fast), but the arxiv
    # corpus operates on a months-to-years timescale. With 30-day half-
    # life, a 6-month-old paper got recency ≈ 0.015 (essentially zero),
    # so 2025 papers and 2018 papers tied. 180-day half-life gives a
    # 6-month paper recency = 0.5, and an 18-month paper = 0.125 — a
    # meaningful spread that matches "what feels recent" in research.
    recency_half_life_days: int = 180     # 2^(-age/half_life) decay
    velocity_min_age_days:  int = 1       # floor for div-by-zero on same-day papers


@dataclass(frozen=True, slots=True)
class StoresParams:
    """Tunables for the 4 stores (Postgres, Qdrant, Neo4j, MinIO).

    Per docs/CODE-CONVENTIONS.md §3: groups loose I/O tunables so a
    callsite passes ONE object instead of a fan-in of imports.
    """
    # Qdrant — must match the embedding model's output dim. The RR uses
    # the existing NIM `nvidia/llama-nemotron-embed-1b-v2` (2048d) via the
    # LLM rotator's embed_via_router_async — see architecture-doc §2.4.2.
    qdrant_vector_dim:       int = 2048
    qdrant_segment_count:    int = 2     # OptimizersConfigDiff.default_segment_number
    qdrant_upsert_batch:     int = 64    # chunk per upsert call

    # Postgres — connection-level timeouts (per-operation; no shared pool yet).
    pg_statement_timeout_ms: int = 30_000

    # MinIO — content type for JSON artifacts. Bytes go directly via aioboto3.
    minio_json_content_type: str = "application/json"


# Module-level singletons — callers prefer these over re-instantiating.
WEIGHTS       = SignalWeights()
DOMAIN_PARAMS = DomainParams()
STORES_PARAMS = StoresParams()
