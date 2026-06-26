"""Pure functions for the RR domain — no I/O, no event loop, no mocks."""
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


_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")


def _canonical_arxiv_id(raw: str | None) -> str | None:
    """Extract 'YYYY.NNNNN' from any form: bare id, versioned, 'arXiv:' prefix, or full URL."""
    if not raw:
        return None
    m = _ARXIV_ID_RE.search(raw)
    return m.group(1) if m else None


def _parse_date(value: Any) -> date | None:
    """Parse heterogeneous date shapes the 4 source tools return; bad input → None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # Year-only fallback (S2 sometimes only has the year)
    try:
        return date(int(s), 1, 1)
    except (ValueError, TypeError):
        return None


def normalize_arxiv(d: dict[str, Any]) -> NormalizedPaper:
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
    author = (d.get("author") or "").strip()
    return NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title") or "").strip(),
        # HN self-post body is the closest analogue to an "abstract"; most HN hits are link posts and this stays empty.
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


_TITLE_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalized_title(title: str) -> str:
    """Secondary dedup key for items without arxiv_id — catches HN crossposts with identical titles."""
    if not title:
        return ""
    return _TITLE_NORM_RE.sub(" ", title.lower()).strip()


def dedup_by_arxiv_id(items: list[NormalizedPaper]) -> list[NormalizedPaper]:
    """Primary dedup on arxiv_id (max-merge signals); secondary dedup by normalized title for no-id items."""
    by_id: dict[str, NormalizedPaper] = {}
    by_title: dict[str, NormalizedPaper] = {}
    no_key: list[NormalizedPaper] = []
    for it in items:
        if it.arxiv_id:
            existing = by_id.get(it.arxiv_id)
            by_id[it.arxiv_id] = _merge(existing, it) if existing else it
            continue
        nt = _normalized_title(it.title)
        if not nt:
            no_key.append(it)
            continue
        existing_t = by_title.get(nt)
        by_title[nt] = _merge(existing_t, it) if existing_t else it
    return list(by_id.values()) + list(by_title.values()) + no_key


def _merge(a: NormalizedPaper, b: NormalizedPaper) -> NormalizedPaper:
    """Max-merge per-source signals; strings/dates prefer first non-empty; sets union."""
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


def signal_score(
    p: NormalizedPaper,
    *,
    now: date,
    profile_embedding: tuple[float, ...] | None = None,
    profile_verticals: tuple[str, ...] = (),
    weights: SignalWeights = WEIGHTS,
    domain_params: DomainParams = DOMAIN_PARAMS,
) -> float:
    """Sortable composite scalar — weights need not sum to 1; each component is roughly [0, 1]."""
    rel = _cosine(profile_embedding, p.embedding) \
        if (profile_embedding and p.embedding) else 0.0
    rec = _recency_decay(p.published, now, domain_params.recency_half_life_days)
    vel = _velocity(p.citations, p.published, now, domain_params.velocity_min_age_days)
    infl = (p.influential_citations / p.citations) if p.citations > 0 else 0.0
    infl = max(0.0, min(infl, 1.0))
    fit = _vertical_fit(p.categories, profile_verticals)
    # log1p caps buzz dominance from one viral HN post; /14 normalizes since log1p(1_000_000) ≈ 13.8.
    buzz_raw = _log1p(p.hn_points) + _log1p(p.hf_upvotes)
    buzz = min(buzz_raw / 14.0, 1.0)
    code = 1.0 if p.has_code else 0.0
    # arxiv_id absence = product announcement; lift prevents displacing real papers on thin result sets.
    has_aid = 1.0 if p.arxiv_id else 0.0
    return (
        weights.relevance         * rel
        + weights.recency         * rec
        + weights.citation_velocity * vel
        + weights.influential_ratio * infl
        + weights.vertical_fit    * fit
        + weights.cross_tier_buzz * buzz
        + weights.has_code        * code
        + weights.has_arxiv_id    * has_aid
    )


def diff_vs_seen(
    candidates: list[NormalizedPaper],
    seen_arxiv_ids: frozenset[str],
) -> tuple[list[NormalizedPaper], list[NormalizedPaper]]:
    """Returns (new, returning). Papers without arxiv_id are always new — no stable identity in radar_seen."""
    new: list[NormalizedPaper] = []
    returning: list[NormalizedPaper] = []
    for c in candidates:
        if c.arxiv_id and c.arxiv_id in seen_arxiv_ids:
            returning.append(c)
        else:
            new.append(c)
    return new, returning


def _cosine(a: tuple[float, ...] | None, b: tuple[float, ...] | None) -> float:
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
    """log1p(citations) / log1p(age_days) — log-saturated citations-per-day proxy, clamped to [0, 1]."""
    if citations <= 0 or published is None:
        return 0.0
    age_days = max((now - published).days, min_age_days)
    denom = math.log1p(age_days)
    if denom <= 0.0:
        return 0.0
    return min(math.log1p(citations) / denom, 1.0)


def _vertical_fit(categories: tuple[str, ...], verticals: tuple[str, ...]) -> float:
    """Fraction of the paper's categories that match profile verticals. Both sides use the same controlled vocabulary."""
    if not categories or not verticals:
        return 0.0
    cats  = {c.lower() for c in categories}
    verts = {v.lower() for v in verticals}
    overlap = cats & verts
    return (len(overlap) / len(cats)) if overlap else 0.0


def _log1p(x: int | float) -> float:
    return math.log1p(x) if x > 0 else 0.0
