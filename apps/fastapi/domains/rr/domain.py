"""Pure functions for the RR domain — no I/O, no event loop, no mocks.

Per docs/CODE-CONVENTIONS.md §domain: this module is the functional core
in the Cosmic Python split. All side effects (Neo4j, Qdrant, Postgres,
MinIO, MCP) live in service.py (step 3). Anything here is deterministic
and unit-testable from a REPL.

Contents (architecture doc §2.5):

  Normalizers          source-specific dict  →  NormalizedPaper
                       (one per source: arxiv · s2 · hf · hn)

  dedup_by_arxiv_id    cross-source merge — same arxiv_id collapses to
                       one NormalizedPaper with max-merged per-source
                       signals (citations · hn_points · hf_upvotes ...)

  signal_score         composite ranking scalar — relevance · recency ·
                       citation_velocity · influential_ratio · vertical_fit
                       · cross_tier_buzz · has_code

  diff_vs_seen         partition candidates into (new, returning) given
                       the profile's seen-set from radar_seen
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

from .entities import NormalizedPaper
from .keys import (
    S2_EXTERNAL_ID_ARXIV,
    SOURCE_ARXIV,
    SOURCE_HF,
    SOURCE_HN,
    SOURCE_S2,
)
from .params import DOMAIN_PARAMS, WEIGHTS, DomainParams, SignalWeights


# --------------------------------------------------------------------------- #
# Canonical arxiv-id parsing — strip version suffix so dedup is stable across
# revisions ('2406.12345v2' and '2406.12345v1' are the same paper).
# --------------------------------------------------------------------------- #
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")


def _canonical_arxiv_id(raw: str | None) -> str | None:
    """Extract the canonical 'YYYY.NNNNN' arxiv id from any input form:
    bare id, versioned id, 'arXiv:' prefix, or full URL. Returns None when
    nothing matches (e.g. an HN story whose URL doesn't reference arxiv)."""
    if not raw:
        return None
    m = _ARXIV_ID_RE.search(raw)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Date parsing — MCP tools return ISO strings via Pydantic JSON-mode; some
# sources only have year resolution. Tolerant — bad input → None, never raises.
# --------------------------------------------------------------------------- #
def _parse_date(value: Any) -> date | None:
    """Parse the heterogeneous date shapes the 4 source tools return."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    # ISO datetime with TZ (arxiv's published comes as 2024-06-12T17:34:21Z)
    if "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    # ISO date (YYYY-MM-DD)
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # Year-only fallback (S2 sometimes only has the year)
    try:
        return date(int(s), 1, 1)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Normalizers — one per source. Each takes the MCP-serialized dict form of
# the source's Paper/Hit shape and returns a NormalizedPaper.
#
# Cross-source dedup happens AFTER normalization: same arxiv_id from multiple
# sources collapses via dedup_by_arxiv_id (below).
# --------------------------------------------------------------------------- #
def normalize_arxiv(d: dict[str, Any]) -> NormalizedPaper:
    """arxiv.Paper → NormalizedPaper. arxiv carries categories + abstract
    but no citation / community signals."""
    return NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = _parse_date(d.get("published")),
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("categories", []) or []),
        citations             = 0,
        influential_citations = 0,
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = 0,
        sources               = frozenset({SOURCE_ARXIV}),
    )


def normalize_s2(d: dict[str, Any]) -> NormalizedPaper:
    """s2.Paper → NormalizedPaper. S2 carries citation_count + influential_
    citation_count (the radar's quality signals) and the cross-source ArXiv
    id via external_ids."""
    external_ids = d.get("external_ids") or {}
    arxiv_raw = external_ids.get(S2_EXTERNAL_ID_ARXIV)
    published = _parse_date(d.get("publication_date")) or _parse_date(d.get("year"))
    return NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(arxiv_raw),
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = published,
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("fields_of_study", []) or []),
        citations             = int(d.get("citation_count")             or 0),
        influential_citations = int(d.get("influential_citation_count") or 0),
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = 0,
        sources               = frozenset({SOURCE_S2}),
    )


def normalize_hf(d: dict[str, Any]) -> NormalizedPaper:
    """hf.Paper → NormalizedPaper. HF Daily Papers always carries arxiv_id
    (by feed design) + community upvotes. arxiv categories are NOT surfaced
    by the HF endpoint."""
    return NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = _parse_date(d.get("published")),
        authors               = tuple(d.get("authors", []) or []),
        categories            = (),
        citations             = 0,
        influential_citations = 0,
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = int(d.get("upvotes") or 0),
        sources               = frozenset({SOURCE_HF}),
    )


def normalize_hn(d: dict[str, Any]) -> NormalizedPaper:
    """hn.Hit → NormalizedPaper. HN carries traction signals (points,
    num_comments) and an EXTRACTED arxiv_id when the story URL points at
    arxiv.org or huggingface.co/papers — the only path by which HN hits
    join the cross-source dedup graph."""
    author = (d.get("author") or "").strip()
    return NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title") or "").strip(),
        # HN self-post body is the closest analogue to an "abstract"; most
        # HN hits are link posts and this stays empty.
        abstract              = (d.get("story_text") or "").strip(),
        published             = _parse_date(d.get("created_at")),
        authors               = (author,) if author else (),
        categories            = tuple(d.get("tags", []) or []),
        citations             = 0,
        influential_citations = 0,
        hn_points             = int(d.get("points")       or 0),
        hn_num_comments       = int(d.get("num_comments") or 0),
        hf_upvotes            = 0,
        sources               = frozenset({SOURCE_HN}),
    )


# --------------------------------------------------------------------------- #
# Cross-source dedup — the architectural payoff. Same arxiv_id from multiple
# sources collapses to one NormalizedPaper whose signal fields are max-merged
# (the source missing each signal contributed 0, so max == "the source that
# has it"). Papers without an arxiv_id can't dedup; they're kept as standalone
# candidates.
# --------------------------------------------------------------------------- #
def dedup_by_arxiv_id(items: list[NormalizedPaper]) -> list[NormalizedPaper]:
    """Merge papers sharing an arxiv_id; pass through those without one.

    Output ordering: dedup-groups first (insertion order of the first
    occurrence of each arxiv_id), then standalone-no-id papers in original
    order.
    """
    by_id: dict[str, NormalizedPaper] = {}
    no_id: list[NormalizedPaper] = []
    for it in items:
        if not it.arxiv_id:
            no_id.append(it)
            continue
        existing = by_id.get(it.arxiv_id)
        by_id[it.arxiv_id] = _merge(existing, it) if existing else it
    return list(by_id.values()) + no_id


def _merge(a: NormalizedPaper, b: NormalizedPaper) -> NormalizedPaper:
    """Combine two NormalizedPapers that share an arxiv_id. Strings/dates
    prefer the first non-empty; per-source signals take max; sets / authors /
    categories union."""
    return NormalizedPaper(
        arxiv_id              = a.arxiv_id,
        title                 = a.title    or b.title,
        abstract              = a.abstract or b.abstract,
        published             = a.published or b.published,
        authors               = a.authors  or b.authors,
        categories            = tuple(sorted(set(a.categories) | set(b.categories))),
        citations             = max(a.citations,             b.citations),
        influential_citations = max(a.influential_citations, b.influential_citations),
        hn_points             = max(a.hn_points,             b.hn_points),
        hn_num_comments       = max(a.hn_num_comments,       b.hn_num_comments),
        hf_upvotes            = max(a.hf_upvotes,            b.hf_upvotes),
        sources               = a.sources | b.sources,
        embedding             = a.embedding or b.embedding,
        has_code              = a.has_code or b.has_code,
    )


# --------------------------------------------------------------------------- #
# Signal score — composite ranking scalar. See architecture-doc §2.5 for the
# weights rationale + per-profile override path.
# --------------------------------------------------------------------------- #
def signal_score(
    p: NormalizedPaper,
    *,
    now: date,
    profile_embedding: tuple[float, ...] | None = None,
    profile_verticals: tuple[str, ...] = (),
    weights: SignalWeights = WEIGHTS,
    domain_params: DomainParams = DOMAIN_PARAMS,
) -> float:
    """Composite signal score in [0, ~1] for ranking. Higher is better.

    The score is a sortable scalar — weights need not sum to 1. Per-term
    each component is roughly [0, 1] so weight magnitudes directly express
    relative influence.
    """
    rel = _cosine(profile_embedding, p.embedding) \
        if (profile_embedding and p.embedding) else 0.0
    rec = _recency_decay(p.published, now, domain_params.recency_half_life_days)
    vel = _velocity(p.citations, p.published, now, domain_params.velocity_min_age_days)
    infl = (p.influential_citations / p.citations) if p.citations > 0 else 0.0
    infl = max(0.0, min(infl, 1.0))
    fit = _vertical_fit(p.categories, profile_verticals)
    # log1p caps buzz dominance from one viral HN post; /14 normalizes since
    # log1p(1_000_000) ≈ 13.8.
    buzz_raw = _log1p(p.hn_points) + _log1p(p.hf_upvotes)
    buzz = min(buzz_raw / 14.0, 1.0)
    code = 1.0 if p.has_code else 0.0
    return (
        weights.relevance         * rel
        + weights.recency         * rec
        + weights.citation_velocity * vel
        + weights.influential_ratio * infl
        + weights.vertical_fit    * fit
        + weights.cross_tier_buzz * buzz
        + weights.has_code        * code
    )


# --------------------------------------------------------------------------- #
# Diff vs seen — partition this scan's candidates into (new, returning) using
# the profile's seen-set from radar_seen. Drives the digest's "New since last
# scan" section.
# --------------------------------------------------------------------------- #
def diff_vs_seen(
    candidates: list[NormalizedPaper],
    seen_arxiv_ids: frozenset[str],
) -> tuple[list[NormalizedPaper], list[NormalizedPaper]]:
    """Returns (new, returning).

    Papers without an arxiv_id are treated as new — they have no stable
    identity in radar_seen, so they're always treated as fresh discoveries.
    """
    new: list[NormalizedPaper] = []
    returning: list[NormalizedPaper] = []
    for c in candidates:
        if c.arxiv_id and c.arxiv_id in seen_arxiv_ids:
            returning.append(c)
        else:
            new.append(c)
    return new, returning


# --------------------------------------------------------------------------- #
# Pure helpers (private)
# --------------------------------------------------------------------------- #
def _cosine(a: tuple[float, ...] | None, b: tuple[float, ...] | None) -> float:
    """Cosine similarity in [-1, 1]; 0 on missing / mismatched-dim inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na  += x * x
        nb  += y * y
    denom = (na ** 0.5) * (nb ** 0.5)
    return (dot / denom) if denom > 0.0 else 0.0


def _recency_decay(published: date | None, now: date, half_life_days: int) -> float:
    """Exponential decay: 2^(-age/half_life). Same-day or future → 1.0."""
    if published is None:
        return 0.0
    age_days = (now - published).days
    if age_days <= 0:
        return 1.0
    return 2.0 ** (-age_days / half_life_days)


def _velocity(citations: int, published: date | None, now: date, min_age_days: int) -> float:
    """log1p(citations) / log1p(age_days) — a citations-per-day proxy that
    log-saturates so the metric stays bounded. Clamped to [0, 1]."""
    if citations <= 0 or published is None:
        return 0.0
    age_days = max((now - published).days, min_age_days)
    denom = math.log1p(age_days)
    if denom <= 0.0:
        return 0.0
    return min(math.log1p(citations) / denom, 1.0)


def _vertical_fit(categories: tuple[str, ...], verticals: tuple[str, ...]) -> float:
    """Fraction of the paper's categories that match the profile's verticals.

    Literal case-insensitive set overlap — the FastHTML profile editor
    (step 6) provides verticals from a controlled list so both sides use
    the same vocabulary. Empty inputs → 0.0.
    """
    if not categories or not verticals:
        return 0.0
    cats  = {c.lower() for c in categories}
    verts = {v.lower() for v in verticals}
    overlap = cats & verts
    return (len(overlap) / len(cats)) if overlap else 0.0


def _log1p(x: int | float) -> float:
    """log(1+x); 0.0 for non-positive input."""
    return math.log1p(x) if x > 0 else 0.0
