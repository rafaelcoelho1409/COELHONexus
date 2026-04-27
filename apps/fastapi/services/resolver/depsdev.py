"""
deps.dev — Google's unified package metadata API (Layer 1.5).

Free, no auth, no documented per-IP rate limit. Covers 7 ecosystems
(GO, RUBYGEMS, NPM, CARGO, MAVEN, PYPI, NUGET) with normalized
`links[]` containing categorized URLs (DOCUMENTATION, HOMEPAGE,
SOURCE_REPO, ISSUE_TRACKER).

Cross-source redundancy with ecosyste.ms — same data, different infra.
ecosyste.ms intermittently 500s on common names (Pydantic, Python,
FastAPI); deps.dev provides resilience for those cases AND returns the
same canonical Documentation URL via PyPI's [project.urls] field
(which Exa search misses, returning SEO-mirrors instead).

CRITICAL gotcha — 2-call pattern required:
The `links` array (with categorized URLs) lives on the GetVersion
endpoint, NOT GetPackage. Implementation:
  1. GET /v3/systems/{ecosystem}/packages/{name}
     → returns versions[]; pick `isDefault: true`
  2. GET /v3/systems/{ecosystem}/packages/{name}/versions/{version}
     → returns links[] with {label, url}

Validated 2026-04-26 against pypi/fastapi==0.115.0 → returns
https://fastapi.tiangolo.com/ correctly (which Exa got wrong via SEO mirror).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


_API = "https://api.deps.dev/v3"
_TIMEOUT_SEC = 8.0
_USER_AGENT = "COELHONexus-resolver/1.0"

# Ecosystem name mapping. ecosyste.ms uses lowercase ('pypi', 'npm');
# deps.dev expects UPPERCASE plus a few aliases. Map both directions
# so callers can pass either form.
_ECOSYSTEM_MAP = {
    "pypi":     "PYPI",
    "npm":      "NPM",
    "cargo":    "CARGO",
    "go":       "GO",
    "rubygems": "RUBYGEMS",
    "maven":    "MAVEN",
    "nuget":    "NUGET",
    # ecosyste.ms-style aliases
    "pypi.org": "PYPI",
    "npmjs.org": "NPM",
    "crates.io": "CARGO",
    "proxy.golang.org": "GO",
    "rubygems.org": "RUBYGEMS",
    "repo1.maven.org": "MAVEN",
    "nuget.org": "NUGET",
}

# Link labels in priority order — first match wins.
_LINK_PRIORITY = ["DOCUMENTATION", "HOMEPAGE", "WEB"]


@dataclass
class DepsDevHit:
    """A canonical URL extracted from deps.dev for one package."""
    ecosystem: str           # 'PYPI' / 'NPM' / etc.
    package_name: str
    version: str             # default version
    docs_url: Optional[str] = None      # picked from DOCUMENTATION/HOMEPAGE
    homepage: Optional[str] = None      # raw HOMEPAGE label if present
    repository_url: Optional[str] = None  # SOURCE_REPO label
    docs_url_label: Optional[str] = None  # which label produced docs_url


def normalize_ecosystem(eco: str) -> Optional[str]:
    """Map ecosyste.ms-style or short ecosystem name → deps.dev system value."""
    if not eco:
        return None
    return _ECOSYSTEM_MAP.get(eco.lower().strip())


async def _fetch_default_version(
    client: httpx.AsyncClient, ecosystem: str, name: str,
) -> Optional[str]:
    """GetPackage → return the version where isDefault: true."""
    url = f"{_API}/systems/{ecosystem}/packages/{quote(name)}"
    try:
        r = await client.get(url, timeout=_TIMEOUT_SEC)
    except httpx.HTTPError as e:
        logger.debug(f"[depsdev] GetPackage error for {ecosystem}/{name}: {e}")
        return None
    if r.status_code != 200:
        logger.debug(f"[depsdev] GetPackage HTTP {r.status_code} for {ecosystem}/{name}")
        return None
    try:
        payload = r.json() or {}
    except ValueError:
        return None
    versions = payload.get("versions") or []
    if not isinstance(versions, list):
        return None
    # First default version. If none flagged default, fall back to last.
    for v in versions:
        if isinstance(v, dict) and v.get("isDefault"):
            vk = v.get("versionKey") or {}
            return vk.get("version") if isinstance(vk, dict) else None
    if versions:
        last = versions[-1]
        if isinstance(last, dict):
            vk = last.get("versionKey") or {}
            return vk.get("version") if isinstance(vk, dict) else None
    return None


async def _fetch_version_links(
    client: httpx.AsyncClient, ecosystem: str, name: str, version: str,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    GetVersion → returns (docs_url, label_used, homepage, repo_url).
    Picks first link by _LINK_PRIORITY for docs_url.
    """
    url = (
        f"{_API}/systems/{ecosystem}/packages/{quote(name)}"
        f"/versions/{quote(version)}"
    )
    try:
        r = await client.get(url, timeout=_TIMEOUT_SEC)
    except httpx.HTTPError as e:
        logger.debug(f"[depsdev] GetVersion error: {e}")
        return None, None, None, None
    if r.status_code != 200:
        return None, None, None, None
    try:
        payload = r.json() or {}
    except ValueError:
        return None, None, None, None
    links = payload.get("links") or []
    if not isinstance(links, list):
        return None, None, None, None

    # Index links by label (deduped — pick first per label).
    by_label: dict[str, str] = {}
    for link in links:
        if not isinstance(link, dict):
            continue
        label = (link.get("label") or "").strip().upper()
        u = link.get("url")
        if label and u and label not in by_label:
            by_label[label] = u

    # Pick docs_url by priority order.
    docs_url: Optional[str] = None
    label_used: Optional[str] = None
    for lbl in _LINK_PRIORITY:
        if lbl in by_label:
            docs_url = by_label[lbl]
            label_used = lbl
            break

    homepage = by_label.get("HOMEPAGE") or by_label.get("WEB")
    repo_url = by_label.get("SOURCE_REPO") or by_label.get("REPO")
    return docs_url, label_used, homepage, repo_url


def _name_variants(name: str) -> list[str]:
    """
    Generate package-name variants in priority order. deps.dev is
    case-sensitive and registries differ on naming convention:
      - PyPI normalizes to lowercase (per PEP 503): `mongodb` not `MongoDB`
      - npm always lowercase
      - CARGO often preserves user case (`MongoDB` is a real CARGO crate)
    Trying multiple variants in parallel adds N requests but deps.dev is
    unmetered. Without this, "MongoDB" hits CARGO (Rust driver) only and
    misses PyPI's `pymongo` because the names differ entirely (note: that's
    a separate problem; here we just rescue case-only mismatches).
    """
    n = name.strip()
    if not n:
        return []
    variants: list[str] = [n]
    lower = n.lower()
    if lower != n:
        variants.append(lower)
    # Dash variant: spaces / underscores → dashes (PEP 503-ish for PyPI).
    dashed = lower.replace(" ", "-").replace("_", "-")
    if dashed not in variants:
        variants.append(dashed)
    return variants


# Ecosystem priority — earlier = preferred when multiple variants/ecosystems hit.
# PyPI/NPM are mainstream-language registries; CARGO often returns Rust
# bindings/drivers that aren't the platform canonical (MongoDB → mongo-rust).
_ECOSYSTEM_PREFERENCE = ["PYPI", "NPM", "GO", "MAVEN", "RUBYGEMS", "NUGET", "CARGO"]


async def lookup_depsdev(
    name: str,
    ecosystem: str | None = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[DepsDevHit]:
    """
    Cross-registry lookup with case-tolerant variant fan-out.

    Strategy:
      - Generate name variants (original, lowercase, dashed)
      - Fan out GetPackage across {variants} × {ecosystems} in parallel
      - Among hits, pick by ecosystem preference (mainstream > Rust/etc.)
        and prefer the variant matching that ecosystem's convention
      - Then ONE GetVersion call for the chosen (ecosystem, variant, version)

    Returns None when no variant/ecosystem combo finds the package.
    """
    if not name or not name.strip():
        return None

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_SEC,
        )

    try:
        ecosystems_to_try: list[str] = []
        if ecosystem:
            normalized = normalize_ecosystem(ecosystem)
            if normalized:
                ecosystems_to_try = [normalized]
        if not ecosystems_to_try:
            ecosystems_to_try = ["PYPI", "NPM", "CARGO", "GO"]

        variants = _name_variants(name)
        # Fan out across (variant × ecosystem). deps.dev is unmetered so the
        # extra calls (max 3 variants × 4 ecosystems = 12) cost nothing and
        # buy case-resilience.
        pairs = [(eco, var) for eco in ecosystems_to_try for var in variants]
        version_results = await asyncio.gather(
            *(_fetch_default_version(client, eco, var) for eco, var in pairs),
            return_exceptions=False,
        )

        # Group hits by ecosystem; for each, prefer the original-cased variant
        # if it hit, else first variant that hit.
        eco_to_hit: dict[str, tuple[str, str]] = {}  # eco → (variant, version)
        for (eco, var), ver in zip(pairs, version_results):
            if ver and eco not in eco_to_hit:
                eco_to_hit[eco] = (var, ver)

        if not eco_to_hit:
            return None

        # Pick by ecosystem preference (mainstream first; CARGO last to avoid
        # Rust-driver capture for vendor-portal queries like MongoDB).
        chosen_eco: Optional[str] = None
        for pref in _ECOSYSTEM_PREFERENCE:
            if pref in eco_to_hit:
                chosen_eco = pref
                break
        if chosen_eco is None:
            chosen_eco = next(iter(eco_to_hit))

        chosen_variant, chosen_version = eco_to_hit[chosen_eco]
        docs_url, label, homepage, repo = await _fetch_version_links(
            client, chosen_eco, chosen_variant, chosen_version,
        )
        if docs_url is None and homepage is None and repo is None:
            return None
        return DepsDevHit(
            ecosystem=chosen_eco,
            package_name=chosen_variant,
            version=chosen_version,
            docs_url=docs_url,
            homepage=homepage,
            repository_url=repo,
            docs_url_label=label,
        )
    finally:
        if own_client and client is not None:
            await client.aclose()
