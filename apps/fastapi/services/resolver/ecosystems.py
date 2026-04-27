"""
ecosyste.ms cross-registry lookup — primary docs URL discovery.

ecosyste.ms aggregates package metadata across 80+ registries (PyPI, npm,
Cargo, Conda, Alpine, Homebrew, Helm, Terraform Registry, Docker Hub, etc.)
and normalizes each entry's `documentation_url`, `homepage`, `repository_url`.

Two query patterns:
  - `lookup_by_name(name)`   → search across ALL registries by package name
  - `lookup_by_repo(url)`    → search across registries pointing to the same repo

Empirical insight (validated 2026-04-26): for INFRASTRUCTURE tools (Docker,
Kubernetes, Helm, Terraform), the canonical docs URL appears in the
`repository_url` field of OS-package-manager entries (Alpine, Homebrew,
Conda) — because those registries store the upstream binary source URL,
which IS the docs domain (helm.sh, kubernetes.io, docker.io). For
LIBRARIES (Pydantic, vLLM, LangChain), `documentation_url` from PyPI/npm
is the right field. The `pick_canonical_url` ranker handles both.

API: free, anonymous, 5000 req/hr per IP. No auth required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)


_API = "https://packages.ecosyste.ms/api/v1"
_TIMEOUT_SEC = 8.0
_USER_AGENT = "COELHONexus-resolver/1.0"


# Generic docs-hosting domains — penalized in ranking because the
# subdomain is just a slug picked by the package maintainer, not a
# vendor-controlled canonical domain. `kubernetes.readthedocs.io` is
# the Python client's docs, NOT Kubernetes the platform's.
_DOCS_HOSTING_SUFFIXES = (
    "readthedocs.io",
    "readthedocs-hosted.com",
    "github.io",
    "gitbook.io",
    "mintlify.app",
    "rubydoc.info",
    "pkg.go.dev",
)

_VCS_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


@dataclass
class EcosystemsHit:
    """One package entry from ecosyste.ms."""
    name: str
    ecosystem: str                          # 'pypi', 'npm', 'conda', 'alpine', 'homebrew', etc.
    description: str = ""
    homepage: Optional[str] = None
    documentation_url: Optional[str] = None
    repository_url: Optional[str] = None
    latest_release_number: Optional[str] = None
    licenses: Optional[str] = None
    versions_count: int = 0


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
async def lookup_by_name(
    name: str, *, client: Optional[httpx.AsyncClient] = None,
) -> list[EcosystemsHit]:
    """
    Cross-registry name search with variant fallback. Returns ALL packages
    matching ANY of the variants tried (in order, stops on first non-empty).

    Why variants: ecosyste.ms /packages/lookup?name= is CASE-SENSITIVE and
    matches registry slugs literally. Spaces never work. Variants:
      1. lowercase original              ('Pydantic' → 'pydantic')
      2. spaces → hyphens                ('Apache Airflow' → 'apache-airflow')
      3. last whitespace-token only      ('Apache Kafka' → 'kafka')
      4. strip ALL hyphens               ('Shap-IQ' → 'shapiq')

    Empirically validated 2026-04-26: variants recover ~16 of 19 frameworks
    that fail on the literal lookup.
    """
    if not name or not name.strip():
        return []

    lc = name.strip().lower()
    hyphenated = lc.replace(" ", "-")
    last_token = lc.rsplit(" ", 1)[-1]
    no_hyphens = lc.replace("-", "").replace(" ", "")

    seen: set[str] = set()
    variants: list[str] = []
    for v in (lc, hyphenated, last_token, no_hyphens):
        if v and v not in seen:
            seen.add(v)
            variants.append(v)

    for variant in variants:
        hits = await _fetch_lookup(
            f"{_API}/packages/lookup?name={quote(variant)}",
            client=client,
        )
        if hits:
            return hits
    return []


async def lookup_by_repo(
    repository_url: str, *, client: Optional[httpx.AsyncClient] = None,
) -> list[EcosystemsHit]:
    """Cross-registry repo-URL search. Returns all packages pointing here."""
    if not repository_url:
        return []
    return await _fetch_lookup(
        f"{_API}/packages/lookup?repository_url={quote(repository_url, safe=':/')}",
        client=client,
    )


async def _fetch_lookup(
    url: str, *, client: Optional[httpx.AsyncClient] = None,
) -> list[EcosystemsHit]:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_SEC,
        )
    try:
        try:
            r = await client.get(url, timeout=_TIMEOUT_SEC)
        except httpx.HTTPError as e:
            logger.warning(f"[resolver.ecosystems] {url} error: {e}")
            return []
        if r.status_code != 200:
            logger.debug(f"[resolver.ecosystems] {url} HTTP {r.status_code}")
            return []
        try:
            payload = r.json()
        except ValueError:
            return []
        if not isinstance(payload, list):
            return []
        return [
            EcosystemsHit(
                name=item.get("name", "") or "",
                ecosystem=item.get("ecosystem", "") or "",
                description=(item.get("description") or "")[:300],
                homepage=item.get("homepage") or None,
                documentation_url=item.get("documentation_url") or None,
                repository_url=item.get("repository_url") or None,
                latest_release_number=item.get("latest_release_number") or None,
                licenses=item.get("licenses") or None,
                versions_count=int(item.get("versions_count") or 0),
            )
            for item in payload
            if isinstance(item, dict)
        ]
    finally:
        if own_client and client is not None:
            await client.aclose()


# ---------------------------------------------------------------------------
# Ranker
# ---------------------------------------------------------------------------
def _normalize(s: str) -> str:
    return s.lower().replace("-", "").replace("_", "").replace(" ", "").replace(".", "")


def _name_matches_domain(name: str, url: Optional[str]) -> bool:
    """
    True when the URL's apex/base domain matches the query name. Strips
    well-known prefixes (docs, www, api, developer) so `docs.pydantic.dev`
    and `pydantic.dev` both count as matches for "pydantic".
    """
    if not url:
        return False
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return False
    parts = netloc.split(".")
    while parts and parts[0] in ("docs", "www", "api", "developer"):
        parts = parts[1:]
    if len(parts) < 2:
        return False
    base = _normalize(parts[0])
    return base == _normalize(name)


def _is_docs_hosting(url: Optional[str]) -> bool:
    if not url:
        return False
    netloc = urlparse(url).netloc.lower()
    return any(netloc.endswith(s) for s in _DOCS_HOSTING_SUFFIXES)


def _is_vcs(url: Optional[str]) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(host in u for host in _VCS_HOSTS)


@dataclass
class RankedURL:
    """One scored URL candidate from a hit's fields."""
    url: str
    field: str         # 'documentation_url' | 'homepage' | 'repository_url'
    hit: EcosystemsHit
    score: float


def _apex(url: Optional[str]) -> str:
    """
    Best-effort apex extraction. github.io/gitbook.io/readthedocs.io are
    multi-tenant — treat their full subdomain as the apex (otherwise every
    project on github.io collapses to one apex). For everything else,
    return the last 2 labels (foo.bar.com → bar.com).
    """
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if any(netloc.endswith(s) for s in _DOCS_HOSTING_SUFFIXES) or netloc in _VCS_HOSTS:
        return netloc
    parts = netloc.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


def _build_apex_vote(hits: list[EcosystemsHit], query_name: str) -> dict[str, int]:
    """
    Cross-registry vote: count how many DISTINCT registries reference the
    same apex domain across homepage/documentation_url/repository_url.

    When N registries' homepages all point at vuejs.org, that apex is the
    canonical truth — beats any single hit's documentation_url field that
    points elsewhere. Distinct-registries-per-apex (not raw count) prevents
    one ecosystem with many forks from dominating.
    """
    apex_to_ecosystems: dict[str, set[str]] = {}
    for h in hits:
        for field in ("homepage", "documentation_url", "repository_url"):
            url = getattr(h, field)
            if not url:
                continue
            if _is_vcs(url):
                continue  # VCS apex (github.com) is uninformative for vendor canonicalization
            ax = _apex(url)
            if not ax:
                continue
            apex_to_ecosystems.setdefault(ax, set()).add(h.ecosystem)
    return {ax: len(ecos) for ax, ecos in apex_to_ecosystems.items()}


def pick_canonical_url(
    hits: list[EcosystemsHit], query_name: str,
) -> Optional[RankedURL]:
    """
    Pick the single most canonical-looking URL across all hits.

    Scoring per (hit, field) candidate:
      +100  domain matches query name (e.g., docker.io for "Docker")
      +50   not a VCS host (github.com / gitlab.com etc.)
      +30   field is `documentation_url`
      -40   docs-hosting subdomain (readthedocs.io, github.io, etc.)
      + min(versions_count, 200) // 10   tie-breaker, capped sublinear
      + 30 × max(0, distinct_registries_voting_for_this_apex - 1)
        ← cross-registry agreement: when N≥2 registries' URLs share the
          same apex, every candidate on that apex gets a strong bump.
          Catches Vue: 4 registries' homepage = vuejs.org → vuejs.org wins
          over a single registry's documentation_url = vue.readthedocs.io.

    Threshold: best score must be ≥ 30 to return; else None.
    """
    if not hits:
        return None

    apex_votes = _build_apex_vote(hits, query_name)

    candidates: list[RankedURL] = []
    for h in hits:
        for field in ("documentation_url", "homepage", "repository_url"):
            url = getattr(h, field)
            if not url:
                continue
            score: float = 0.0
            if _name_matches_domain(query_name, url):
                score += 100
            if not _is_vcs(url):
                score += 50
            if field == "documentation_url":
                score += 30
            if _is_docs_hosting(url):
                score -= 40
            score += min(h.versions_count, 200) // 10
            apex_n = apex_votes.get(_apex(url), 1)
            score += 30 * max(0, apex_n - 1)
            candidates.append(RankedURL(url=url, field=field, hit=h, score=score))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c.score, reverse=True)
    best = candidates[0]
    if best.score < 30:
        return None
    return best
