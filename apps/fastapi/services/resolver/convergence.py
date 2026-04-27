"""
RRF (Reciprocal Rank Fusion) convergence — merge ranked URL candidates
from heterogeneous sources into a single best pick.

Production-grade: same algorithm Elasticsearch / OpenSearch / Azure AI
Search use for hybrid retrieval (dense + sparse + BM25). Handles
heterogeneous score scales without manual weight calibration.

Pipeline:
  1. Each source contributes 0+ ranked URL candidates (catalog, llms.txt,
     ecosyste.ms, search-rotator).
  2. URLs canonicalized (strip trailing /, lowercase host, drop fragment).
  3. RRF score per unique URL: Σ over sources [ 1 / (k + rank_in_source) ]
     where k=60 (industry standard).
  4. Hard gates applied AFTER scoring — D0 must be LIVE, name token in
     domain OR ≥2 docs signals.
  5. Convention bumps before fusion: /docs/ in path → bump rank by -0.5;
     docs.* host → bump rank by -0.5 (rank lower = better).
  6. Return highest-scoring URL above threshold; else low-confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


# RRF dampening constant — 60 is the Elasticsearch / Azure default.
# Larger k → flatter curve (small per-rank differences); smaller k →
# steeper (rank 1 dominates more).
_RRF_K = 60

# Minimum RRF score to return a URL with confidence. Single-source baseline
# is 1/(60+1) ≈ 0.0164; multi-source agreement is 2/(60+1) ≈ 0.0328. We accept
# single-source as confident IF the candidate also passes the hard gates
# (D0 LIVE + name-in-domain OR ≥2 docs_signals) — those gates already filter
# out garbage. So threshold sits just below the single-source baseline.
_RRF_THRESHOLD = 0.015


@dataclass
class CandidateURL:
    """One URL contributed by one source, with source-local rank."""
    url: str
    source: str            # 'catalog' | 'llmstxt' | 'ecosystems' | 'depsdev' | 'search:exa' | etc.
    rank: int = 1          # 1 = top result of this source
    notes: str = ""
    # Optional sub-source signal: for ecosystems/depsdev, which field
    # produced the URL — strengthens the publisher-asserted tiebreaker.
    # Examples: 'documentation_url', 'DOCUMENTATION', 'HOMEPAGE', 'homepage'
    field: Optional[str] = None


@dataclass
class FusedCandidate:
    """RRF-fused result: one canonical URL, contributors, gates, score."""
    canonical_url: str
    rrf_score: float
    contributors: list[CandidateURL] = field(default_factory=list)
    docs_signals: int = 0          # filled by D0 after fusion
    liveness_status: str = ""      # filled by D0 after fusion
    final_url: Optional[str] = None  # post-redirect URL from D0
    name_in_domain: bool = False
    docs_path: bool = False
    docs_host: bool = False
    rejected: bool = False
    rejection_reason: str = ""


def _canonicalize(url: str) -> str:
    """
    Reduce URLs to a comparable canonical form so cross-source dedup works.
      - lowercase scheme + host
      - strip 'www.' prefix
      - strip trailing slash
      - drop fragment
      - drop query (we only care about the docs root)
    """
    if not url:
        return ""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, netloc, path, "", "", ""))


def _name_in_domain(name: str, url: str) -> bool:
    """
    True if the framework name appears as a token in the URL's apex/sub
    domain. e.g., "Docker" in "docs.docker.com" → True; in
    "docker-py.readthedocs.io" → False (different lib).
    """
    if not name or not url:
        return False
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return False
    if not netloc:
        return False
    parts = netloc.split(".")
    while parts and parts[0] in ("docs", "www", "api", "developer"):
        parts = parts[1:]
    if len(parts) < 2:
        return False
    base = parts[0].replace("-", "").replace("_", "")
    name_norm = name.lower().replace("-", "").replace("_", "").replace(" ", "")
    return base == name_norm


def _has_docs_path(url: str) -> bool:
    try:
        return "/docs/" in (urlparse(url).path or "").lower() or \
               (urlparse(url).path or "").lower().endswith("/docs")
    except ValueError:
        return False


def _has_docs_host(url: str) -> bool:
    try:
        return urlparse(url).netloc.lower().startswith("docs.")
    except ValueError:
        return False


# Tiebreaker priority: higher source_priority = stronger publisher signal.
# Used as a small additive bump (NOT a hard gate) so two candidates with
# similar RRF scores break in favor of the more authoritative source.
_SOURCE_PRIORITY = {
    "catalog":     6,   # hand-curated wins everything
    "llmstxt-hub": 5,   # publisher-asserted (maintainer published llms.txt)
    "depsdev":     4,   # Google's normalized manifest field
    "ecosystems":  3,   # ecosyste.ms
    "llmstxt-probe": 3, # direct {docs_url}/llms.txt validated probe
}
# Field-level priority within depsdev / ecosystems — DOCUMENTATION beats HOMEPAGE.
_FIELD_PRIORITY = {
    "documentation_url": 3,
    "DOCUMENTATION":     3,
    "HOMEPAGE":          2,
    "homepage":          2,
    "WEB":               1,
}
# Tiny per-step score increment — keeps RRF curve dominant; tiebreakers
# only swing close calls. RRF single-source baseline is ≈0.0164;
# bumps top out at ~0.0030 (about 18% of one rank-step).
_TIEBREAK_STEP = 0.0005


_VCS_HOST_TOKENS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


def _is_vcs_url(url: str) -> bool:
    if not url:
        return False
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return any(h in netloc for h in _VCS_HOST_TOKENS)


def _source_priority_bump(
    contributors: list[CandidateURL], canonical_url: str = "",
) -> float:
    """
    Sum publisher-source priority across contributors — multi-source wins.

    BUT: when the URL itself is on a VCS host (github.com / gitlab.com),
    return 0. A registry's HOMEPAGE field pointing at a GitHub repo is
    NOT a docs signal worth boosting over a search-found vendor docs site.
    The VCS URL still contributes to RRF baseline + can win on cross-source
    agreement (multiple sources at github URL = real canonical for tiny libs);
    it just doesn't get the publisher-asserted tiebreaker bump.
    """
    if not contributors:
        return 0.0
    if canonical_url and _is_vcs_url(canonical_url):
        return 0.0
    best_per_source: dict[str, int] = {}
    for c in contributors:
        # Treat 'search:exa' / 'search:tavily' as the same family.
        src = c.source.split(":", 1)[0]
        prio = _SOURCE_PRIORITY.get(src, 0)
        # Field bumps stack on top (DOCUMENTATION over HOMEPAGE).
        field_prio = _FIELD_PRIORITY.get(c.field or "", 0) if c.field else 0
        best_per_source[src] = max(best_per_source.get(src, 0), prio + field_prio)
    return sum(best_per_source.values()) * _TIEBREAK_STEP


def _domain_depth(url: str) -> int:
    """Count subdomain segments (excluding TLD-ish parts). docs.foo.com → 3."""
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return 0
    if not netloc:
        return 0
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return len(netloc.split("."))


def _path_specificity_bump(url: str) -> float:
    """Reward URLs that point at a docs sub-path over bare root."""
    try:
        path = (urlparse(url).path or "").rstrip("/")
    except ValueError:
        return 0.0
    if not path or path == "":
        return 0.0
    bump = 0.0
    lower = path.lower()
    if lower.endswith(("/docs", "/latest", "/stable")) or "/docs/" in lower:
        bump += _TIEBREAK_STEP * 2
    elif "/" in path:
        bump += _TIEBREAK_STEP  # any sub-path beats bare root
    return bump


def _docs_signals_bump(docs_signals: int) -> float:
    """More observed docs signals = stronger docs page. Capped at 4 to avoid runaway."""
    return min(docs_signals, 4) * _TIEBREAK_STEP


def fuse_and_pick(
    candidates: list[CandidateURL],
    *,
    framework: str,
    d0_results: dict[str, dict] | None = None,
) -> Optional[FusedCandidate]:
    """
    Run RRF + hard-gate filtering. `d0_results` is an optional dict
    mapping canonical_url → {status, docs_signals, final_url} from D0
    liveness probes (caller does these in parallel BEFORE calling fuse).

    Returns the best FusedCandidate above threshold, or None when no
    candidate clears both the score threshold AND the hard gates.
    """
    if not candidates:
        return None

    d0_results = d0_results or {}

    # Group by canonical URL.
    grouped: dict[str, list[CandidateURL]] = {}
    for c in candidates:
        if not c.url:
            continue
        canon = _canonicalize(c.url)
        if not canon:
            continue
        grouped.setdefault(canon, []).append(c)

    if not grouped:
        return None

    # Score each canonical URL via RRF, with convention pre-rank bumps.
    fused: list[FusedCandidate] = []
    for canon, contribs in grouped.items():
        score = 0.0
        for c in contribs:
            # Convention bumps decrease effective rank (rank 1 - 0.5 → 0.5),
            # making /docs/ + docs.* URLs win when sources are otherwise tied.
            effective_rank = float(c.rank)
            if _has_docs_path(canon):
                effective_rank -= 0.5
            if _has_docs_host(canon):
                effective_rank -= 0.5
            effective_rank = max(0.5, effective_rank)
            score += 1.0 / (_RRF_K + effective_rank)

        # Tiebreaker bumps — small additive on top of RRF.
        score += _source_priority_bump(contribs, canon)
        score += _path_specificity_bump(canon)
        d0 = d0_results.get(canon, {})
        score += _docs_signals_bump(int(d0.get("docs_signals", 0)))

        cand = FusedCandidate(
            canonical_url=canon,
            rrf_score=score,
            contributors=contribs,
            docs_signals=int(d0.get("docs_signals", 0)),
            liveness_status=str(d0.get("status", "")),
            final_url=d0.get("final_url"),
            name_in_domain=_name_in_domain(framework, canon),
            docs_path=_has_docs_path(canon),
            docs_host=_has_docs_host(canon),
        )
        fused.append(cand)

    # Apply hard gates (rejections).
    for c in fused:
        if c.liveness_status in ("DEAD", "PARKED", "ERROR"):
            c.rejected = True
            c.rejection_reason = f"D0 status = {c.liveness_status}"
            continue
        # Require name-in-domain OR ≥2 docs signals.
        if not c.name_in_domain and c.docs_signals < 2:
            c.rejected = True
            c.rejection_reason = (
                f"name '{framework}' not in domain AND only {c.docs_signals} docs signals"
            )

    # Pick the highest-scoring non-rejected candidate above threshold.
    eligible = [c for c in fused if not c.rejected]
    if not eligible:
        return None
    # Sort by RRF score (incl. tiebreaker bumps); for true ties (same score),
    # prefer deeper subdomain — addresses SEO-mirror ambiguity (fastapi.org
    # vs fastapi.tiangolo.com both score similarly when both are LIVE +
    # name-in-domain; the publisher-asserted hub entry already pumps
    # fastapi.tiangolo.com via _SOURCE_PRIORITY but if it ever doesn't,
    # the 3-segment domain still beats the 2-segment SEO mirror).
    eligible.sort(
        key=lambda c: (c.rrf_score, _domain_depth(c.canonical_url)),
        reverse=True,
    )
    best = eligible[0]
    if best.rrf_score < _RRF_THRESHOLD:
        # Below confidence threshold but still the best we have — return
        # with an explicit low-confidence flag in the rejection_reason field
        # (caller can decide to surface to user vs. add to catalog).
        best.rejection_reason = (
            f"low confidence (RRF score {best.rrf_score:.4f} < threshold {_RRF_THRESHOLD})"
        )
    return best
