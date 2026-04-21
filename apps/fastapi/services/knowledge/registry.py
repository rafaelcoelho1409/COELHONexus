"""
Knowledge Distiller — Package Registry Version Listing

Per-language registry probes. Given a framework name + detected language,
fetch the list of available versions from the authoritative package index
(PyPI for Python, npm for JS/TS, crates.io for Rust, etc.).

Used by `/studies/resolve` to:
  - Populate `available_versions` so the caller sees real versions, not
    guesses.
  - Validate a user-requested version exists before crawling docs for it.
  - Surface alternatives when a requested version isn't available.

Zero auth, modest traffic, short timeouts. Safe to call synchronously
from FastAPI handlers.

Return shape (`RegistryListing`):
  {
    "latest_stable": "3.0.0",             # current stable per the registry's definition
    "all": ["3.0.0", "2.11.1", ...],      # newest-first, capped to ~50
    "source": "pypi",                     # which registry answered
    "fetched_at": "2026-04-20T19:30:00Z",
  }

Returns None when:
  - language is missing or unsupported
  - package not found in the registry (404)
  - registry is unreachable (timeout / 5xx)
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from schemas.knowledge.resolver import RegistryHint


logger = logging.getLogger(__name__)


_TIMEOUT_SECONDS = 5.0
_MAX_VERSIONS = 50


class RegistryListing(BaseModel):
    """Authoritative version listing from a package registry."""
    latest_stable: Optional[str] = Field(
        default = None,
        description = "Latest stable release per the registry (may be None if all releases are pre-releases)."
    )
    all: list[str] = Field(
        default_factory = list,
        description = "All known versions, newest-first. Capped to ~50 to keep responses readable."
    )
    source: str = Field(
        description = "Which registry answered: 'pypi', 'npm', 'crates.io', 'rubygems', 'go'."
    )
    fetched_at: str = Field(
        description = "ISO-8601 timestamp (UTC) of the registry call."
    )


# =============================================================================
# Registry Hint — existence-only lookup for the resolver v2
# =============================================================================
# Unlike list_versions (which returns only version lists), hint_lookup also
# extracts the canonical homepage + repo URLs declared by the publisher.
# The resolver uses these as EVIDENCE passed to the LLM rerank stage — it
# does NOT derive docs URLs from registries. Publishers version their docs
# sites with incompatible conventions, so delegating "find the canonical URL"
# to the LLM + SearXNG beats a hard-coded map.
async def hint_lookup(
    framework: str,
    language: Optional[str] = None) -> RegistryHint:
    """
    Cross-ecosystem existence check. Returns:
      - exists=True + homepage + repo + latest_version + all_versions
      - exists=False when every registry probed returns 404

    Probes in order of likelihood based on the language hint, then falls
    through to the remaining registries. Any one success returns early.
    Network errors surface as exists=False (conservative — the caller falls
    back to SearXNG-only resolution when the registry is unreachable).
    """
    lang = (language or "").strip().lower()
    # Prioritize based on language hint
    if lang in ("python", "py"):
        order = [_pypi_hint, _npm_hint, _crates_hint]
    elif lang in ("javascript", "typescript", "js", "ts", "node", "nodejs"):
        order = [_npm_hint, _pypi_hint, _crates_hint]
    elif lang in ("rust", "rs"):
        order = [_crates_hint, _pypi_hint, _npm_hint]
    else:
        # No hint — try all three; first hit wins
        order = [_pypi_hint, _npm_hint, _crates_hint]
    for probe in order:
        try:
            hint = await probe(framework)
            if hint and hint.exists:
                return hint
        except Exception as e:
            logger.info(f"[registry] {probe.__name__} probe failed for {framework!r}: {e}")
            continue
    return RegistryHint(exists = False)


async def _pypi_hint(framework: str) -> Optional[RegistryHint]:
    norm = re.sub(r"[-_.]+", "-", framework.lower())
    url = f"https://pypi.org/pypi/{norm}/json"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    info = data.get("info") or {}
    releases = data.get("releases") or {}
    live = [v for v, files in releases.items() if files]
    live.sort(key = _pypi_sort_key, reverse = True)
    # PyPI `project_urls` is where publishers put Homepage/Documentation/Source
    project_urls = info.get("project_urls") or {}
    # Prefer explicit Documentation link; fall back to Homepage
    homepage = (
        project_urls.get("Documentation")
        or project_urls.get("documentation")
        or info.get("home_page")
        or project_urls.get("Homepage")
    )
    repo = (
        project_urls.get("Source")
        or project_urls.get("Source Code")
        or project_urls.get("Repository")
        or project_urls.get("GitHub")
    )
    return RegistryHint(
        exists = True,
        homepage = homepage or None,
        repo = repo or None,
        latest_version = info.get("version"),
        all_versions = live[:30],
        source = "pypi",
    )


async def _npm_hint(framework: str) -> Optional[RegistryHint]:
    from urllib.parse import quote
    encoded = quote(framework, safe = "@")
    url = f"https://registry.npmjs.org/{encoded}"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    versions = list((data.get("versions") or {}).keys())
    versions.sort(key = _pypi_sort_key, reverse = True)
    dist_tags = data.get("dist-tags") or {}
    latest = dist_tags.get("latest")
    latest_ver_block = (data.get("versions") or {}).get(latest or "", {})
    homepage = (
        data.get("homepage")
        or latest_ver_block.get("homepage")
    )
    repo_raw = data.get("repository") or latest_ver_block.get("repository")
    if isinstance(repo_raw, dict):
        repo = repo_raw.get("url")
    elif isinstance(repo_raw, str):
        repo = repo_raw
    else:
        repo = None
    return RegistryHint(
        exists = True,
        homepage = homepage or None,
        repo = repo or None,
        latest_version = latest,
        all_versions = versions[:30],
        source = "npm",
    )


async def _crates_hint(framework: str) -> Optional[RegistryHint]:
    norm = framework.lower().replace(" ", "-").replace("_", "-")
    url = f"https://crates.io/api/v1/crates/{norm}"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    crate = data.get("crate") or {}
    versions = [v["num"] for v in (data.get("versions") or []) if not v.get("yanked")]
    return RegistryHint(
        exists = True,
        homepage = crate.get("documentation") or crate.get("homepage"),
        repo = crate.get("repository"),
        latest_version = crate.get("max_stable_version") or crate.get("max_version"),
        all_versions = versions[:30],
        source = "crates.io",
    )


# =============================================================================
# Language → registry dispatcher
# =============================================================================
async def list_versions(
    framework: str,
    language: Optional[str]) -> Optional[RegistryListing]:
    """
    Resolve the list of available versions for `framework` in `language`.
    Returns None if the language has no supported registry or the package
    isn't found.
    """
    lang = (language or "").strip().lower()
    # Normalize framework name once
    name = framework.strip()
    try:
        if lang in ("python", "py"):
            return await _pypi(name)
        if lang in ("javascript", "typescript", "js", "ts", "node", "nodejs"):
            return await _npm(name)
        if lang in ("rust", "rs"):
            return await _crates_io(name)
        if lang in ("ruby", "rb"):
            return await _rubygems(name)
        if lang in ("go", "golang"):
            return await _go_proxy(name)
    except Exception as e:
        logger.warning(
            f"[registry] {lang} lookup for '{framework}' failed: {e}"
        )
        return None
    return None


# =============================================================================
# PyPI — Python
# =============================================================================
def _pypi_sort_key(v: str) -> tuple:
    """
    Best-effort PEP-440-ish sort key. Not a full parser — just enough to
    get numeric-major-first ordering right for the vast majority of packages.
    """
    parts = re.split(r"[.\-+]", v)
    key = []
    for p in parts:
        m = re.match(r"^(\d+)(.*)", p)
        if m:
            key.append((int(m.group(1)), m.group(2) or ""))
        else:
            key.append((-1, p))
    return tuple(key)


async def _pypi(name: str) -> Optional[RegistryListing]:
    # PyPI normalizes package names to lowercase with hyphens (PEP 503)
    norm = re.sub(r"[-_.]+", "-", name.lower())
    url = f"https://pypi.org/pypi/{norm}/json"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        logger.info(f"[registry] pypi 404: {norm!r} not found")
        return None
    r.raise_for_status()
    data = r.json()
    releases = data.get("releases", {}) or {}
    # Skip versions with no release files (yanked / reserved)
    live = [v for v, files in releases.items() if files]
    live.sort(key = _pypi_sort_key, reverse = True)
    return RegistryListing(
        latest_stable = (data.get("info") or {}).get("version"),
        all = live[:_MAX_VERSIONS],
        source = "pypi",
        fetched_at = datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# npm — JavaScript / TypeScript
# =============================================================================
async def _npm(name: str) -> Optional[RegistryListing]:
    # npm accepts scoped packages (`@scope/name`) — URL-encode the slash
    from urllib.parse import quote
    encoded = quote(name, safe = "@")
    url = f"https://registry.npmjs.org/{encoded}"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        logger.info(f"[registry] npm 404: {name!r} not found")
        return None
    r.raise_for_status()
    data = r.json()
    versions = list((data.get("versions") or {}).keys())
    dist_tags = data.get("dist-tags") or {}
    # Sort semver-ish: PyPI's sort key works as a first approximation
    versions.sort(key = _pypi_sort_key, reverse = True)
    return RegistryListing(
        latest_stable = dist_tags.get("latest"),
        all = versions[:_MAX_VERSIONS],
        source = "npm",
        fetched_at = datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# crates.io — Rust
# =============================================================================
async def _crates_io(name: str) -> Optional[RegistryListing]:
    norm = name.lower().replace(" ", "-").replace("_", "-")
    url = f"https://crates.io/api/v1/crates/{norm}"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        logger.info(f"[registry] crates.io 404: {norm!r} not found")
        return None
    r.raise_for_status()
    data = r.json()
    crate = data.get("crate") or {}
    versions = [v["num"] for v in (data.get("versions") or []) if not v.get("yanked")]
    # crates.io returns newest-first already
    return RegistryListing(
        latest_stable = crate.get("max_stable_version") or crate.get("max_version"),
        all = versions[:_MAX_VERSIONS],
        source = "crates.io",
        fetched_at = datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# RubyGems — Ruby
# =============================================================================
async def _rubygems(name: str) -> Optional[RegistryListing]:
    norm = name.lower().replace(" ", "-")
    url = f"https://rubygems.org/api/v1/versions/{norm}.json"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        logger.info(f"[registry] rubygems 404: {norm!r} not found")
        return None
    r.raise_for_status()
    data = r.json()
    # data is a list of version objects, newest-first by default
    versions = [v["number"] for v in data if not v.get("yanked")]
    # First non-prerelease version is the latest stable
    latest_stable = next(
        (v["number"] for v in data if not v.get("yanked") and not v.get("prerelease")),
        None,
    )
    return RegistryListing(
        latest_stable = latest_stable,
        all = versions[:_MAX_VERSIONS],
        source = "rubygems",
        fetched_at = datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# Go proxy — for Go modules
# =============================================================================
async def _go_proxy(name: str) -> Optional[RegistryListing]:
    # Go module paths are case-sensitive and include slashes.
    # The module proxy exposes: https://proxy.golang.org/<module>/@v/list
    # Response: plaintext, newline-delimited list of version tags.
    url = f"https://proxy.golang.org/{name.lower()}/@v/list"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        logger.info(f"[registry] go proxy 404: {name!r} not found")
        return None
    r.raise_for_status()
    lines = [v.strip() for v in r.text.splitlines() if v.strip()]
    lines.sort(key = _pypi_sort_key, reverse = True)
    latest_stable = next(
        (v for v in lines if "-" not in v),
        lines[0] if lines else None,
    )
    return RegistryListing(
        latest_stable = latest_stable,
        all = lines[:_MAX_VERSIONS],
        source = "go",
        fetched_at = datetime.now(timezone.utc).isoformat(),
    )
